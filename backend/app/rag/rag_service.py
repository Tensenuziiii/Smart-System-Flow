"""RAG service — retrieval + grounded answer generation."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.models import ArchitectureEdge, ArchitectureNode, CodeChunk, File, Symbol
from app.redaction.redactor import redact

settings = get_settings()

# ── In-scope heuristic ────────────────────────────────────────────────────────
# Questions that clearly cannot be answered from a codebase get abstained immediately
_OUTOFSCOPE_PATTERNS = [
    r"\b(capital of|president of|weather in|population of|who is|birthday of|when was .* born|what year did)\b",
    r"\b(recipe|cook|restaurant|movie|song|sport|celebrity|politics)\b",
    r"\b(stock price|exchange rate|currency|forex)\b",
    r"\b(?:company|business)\b.{0,30}\b(?:revenue|sales|profit)\b.{0,30}\b(?:forecast|projection|outlook)\b",
]
_OUTOFSCOPE_RE = re.compile("|".join(_OUTOFSCOPE_PATTERNS), re.I)
_GREETING_RE = re.compile(r"^(?:hi|hello|hey|good morning|good afternoon|good evening)[!. ]*$", re.I)

REPO_WALKTHROUGH_SYSTEM_PROMPT = """You are a Senior Staff Engineer giving an experienced new hire a technical walkthrough of this repository.

CONSTRAINTS & RULES:
1. GROUNDING ONLY: You strictly only know what is present in the provided STATIC ANALYSIS CONTEXT below. Never fabricate files, functions, routes, or imports.
2. AUDIENCE: The user is an experienced software engineer. Do NOT explain generic software concepts, languages, or framework basics. Focus purely on this codebase's specific implementation details, flow, and architecture.
3. STRUCTURE: Lead with a 1-2 sentence high-level overview. Then walk through the flow (Entrypoint -> Execution Flow -> Core Modules). Use concise bullet points and bold file paths.
4. UNCERTAINTY & BLIND SPOTS: If static analysis lacks data (e.g., dynamic runtime behavior, environment variables, external API contracts), explicitly state: "Cannot be determined from static analysis data alone because [reason]."
5. TONE: Professional, objective, direct. Avoid conversational filler ("Hello!", "I'd be happy to help"), marketing hype, or condescending explanations.

STATIC ANALYSIS CONTEXT:
{repo_analysis_context}
"""


def _is_out_of_scope(question: str) -> bool:
    return bool(_OUTOFSCOPE_RE.search(question))


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class Evidence:
    file_path: str
    symbol_name: Optional[str]
    line_start: int
    line_end: int
    snippet: str
    confidence: float


@dataclass
class AskResponse:
    answer: str
    evidence: List[Evidence]
    confidence: float
    abstained: bool
    request_id: Optional[str] = None


def _load_repository_summary(repository_id: str) -> Optional[str]:
    summary_path = Path(settings.PARSE_CACHE_DIR) / "repositories" / f"{repository_id}.json"
    try:
        if summary_path.exists():
            return json.dumps(json.loads(summary_path.read_text(encoding="utf-8")), indent=2)[:16000]
    except (OSError, TypeError, ValueError):
        return None
    return None


async def _get_repository_summary(db: AsyncSession, repository_id: str) -> Optional[str]:
    cached = _load_repository_summary(repository_id)
    if cached:
        return cached

    files = (await db.execute(select(File).where(File.repository_id == repository_id))).scalars().all()
    symbols = (await db.execute(select(Symbol).where(Symbol.repository_id == repository_id))).scalars().all()
    nodes = (await db.execute(select(ArchitectureNode).where(ArchitectureNode.repository_id == repository_id))).scalars().all()
    edges = (await db.execute(select(ArchitectureEdge).where(ArchitectureEdge.repository_id == repository_id))).scalars().all()
    if not files and not symbols and not nodes:
        return None

    top_level_modules: Dict[str, int] = {}
    file_path_by_id = {file.id: file.path for file in files}
    for file in files:
        top = file.path.replace("\\", "/").split("/", 1)[0]
        top_level_modules[top] = top_level_modules.get(top, 0) + 1
    node_paths = {node.id: (node.metadata_ or {}).get("path", node.label) for node in nodes}
    summary = {
        "total_files": len(files),
        "languages": {language: sum(1 for file in files if (file.language or "unknown") == language) for language in sorted({file.language or "unknown" for file in files})},
        "top_level_modules": dict(sorted(top_level_modules.items(), key=lambda item: (-item[1], item[0]))[:100]),
        "entry_points": [{"name": symbol.name, "file": file_path_by_id.get(symbol.file_id, "unknown"), "type": symbol.type}
                         for symbol in symbols if symbol.type == "route" or symbol.name.lower().split(".")[-1] in {"main", "app", "run", "start", "cli"}][:100],
        "relationships": [{"source": node_paths.get(edge.source_node_id, edge.source_node_id), "target": node_paths.get(edge.target_node_id, edge.target_node_id), "type": edge.rel_label, "evidence": edge.evidence} for edge in edges[:250]],
        "symbol_count": len(symbols),
        "relationship_count": len(edges),
    }
    return json.dumps(summary, indent=2)[:16000]


async def _get_embedding(text_input: str) -> Optional[List[float]]:
    """Call OpenAI embedding API; return None if unavailable."""
    if not settings.AI_AVAILABLE or not settings.OPENAI_API_KEY:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)
        resp = await client.embeddings.create(model=settings.EMBEDDING_MODEL, input=text_input)
        return resp.data[0].embedding
    except Exception:
        return None


async def retrieve_chunks(
    db: AsyncSession,
    repository_id: str,
    question: str,
    top_k: int = 8,
    focus_file_path: Optional[str] = None,
) -> List[CodeChunk]:
    """
    Hybrid retrieval:
    1. Keyword match (always works even without AI)
    2. Vector similarity (when embeddings are stored)
    """
    # Keyword-based retrieval — always available
    keywords = [w.lower() for w in re.findall(r"\w+", question) if len(w) > 2]
    stmt = select(CodeChunk).where(CodeChunk.repository_id == repository_id)
    result = await db.execute(stmt)
    all_chunks: List[CodeChunk] = result.scalars().all()

    # Score each chunk by keyword overlap
    scored = []
    for chunk in all_chunks:
        content_lower = chunk.content.lower()
        meta = chunk.metadata_ or {}
        context = content_lower + " " + str(meta).lower()
        score = sum(1 for kw in keywords if kw in context)
        if focus_file_path and meta.get("file_path") == focus_file_path:
            score += top_k
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: -x[0])
    top_chunks = [c for _, c in scored[:top_k]]

    return top_chunks


async def answer_question(
    db: AsyncSession,
    repository_id: str,
    question: str,
    request_id: Optional[str] = None,
    analysis_context: Optional[str] = None,
    focus_file_path: Optional[str] = None,
) -> AskResponse:
    """
    Answer a question grounded entirely in retrieved code chunks.
    If confidence < threshold or question is out-of-scope → abstain.
    """
    # ── Out-of-scope check ─────────────────────────────────────────────────
    if _is_out_of_scope(question):
        return AskResponse(
            answer="I don't have enough evidence in this repository to answer that question. "
                   "It appears to be outside the scope of this codebase.",
            evidence=[],
            confidence=0.0,
            abstained=True,
            request_id=request_id,
        )

    if _GREETING_RE.fullmatch(question.strip()):
        return AskResponse(
            answer="Hello. Ask me about this repository's files, functions, classes, routes, or architecture.",
            evidence=[],
            confidence=1.0,
            abstained=False,
            request_id=request_id,
        )

    # ── Retrieve chunks ────────────────────────────────────────────────────
    chunks = await retrieve_chunks(
        db, repository_id, question,
        top_k=settings.TOP_K_RETRIEVAL,
        focus_file_path=focus_file_path,
    )

    repository_summary = await _get_repository_summary(db, repository_id)
    if not chunks and not repository_summary:
        return AskResponse(
            answer="I don't have enough evidence in this repository to answer that question.",
            evidence=[],
            confidence=0.0,
            abstained=True,
            request_id=request_id,
        )

    # ── Build evidence list ────────────────────────────────────────────────
    evidence_list: List[Evidence] = []
    context_parts: List[str] = []

    for chunk in chunks:
        meta = chunk.metadata_ or {}
        file_path = meta.get("file_path", "unknown")
        symbol_name = meta.get("symbol_name")
        line_start = meta.get("line_start", 0)
        line_end = meta.get("line_end", 0)
        snippet = chunk.content[:500]

        evidence_list.append(Evidence(
            file_path=file_path,
            symbol_name=symbol_name,
            line_start=line_start,
            line_end=line_end,
            snippet=snippet,
            confidence=0.9,
        ))
        context_parts.append(
            f"# File: {file_path}"
            + (f" | Symbol: {symbol_name}" if symbol_name else "")
            + f"\n{snippet}\n"
        )

    if repository_summary:
        context_parts.insert(0, f"# Condensed repository analysis summary\n{repository_summary}")
    if analysis_context:
        context_parts.insert(0, f"# Focused repository analysis context\n{analysis_context[:8000]}")
    context = "\n---\n".join(context_parts)

    # ── AI generation or static fallback ──────────────────────────────────
    if settings.AI_AVAILABLE and settings.OPENAI_API_KEY:
        answer, confidence = await _generate_answer_ai(question, context)
    else:
        answer, confidence = _generate_answer_static(question, chunks, repository_summary)

    if confidence < settings.ABSTAIN_CONFIDENCE:
        return AskResponse(
            answer="I don't have enough evidence in this repository to answer that confidently.",
            evidence=evidence_list,
            confidence=confidence,
            abstained=True,
            request_id=request_id,
        )

    answer_with_footnote = answer
    if not answer_with_footnote.lower().startswith("i don't have enough evidence") and evidence_list:
        unique_files = sorted({e.file_path for e in evidence_list if e.file_path})
        answer_with_footnote += (
            f"\n\n_Based on analysis of {len(unique_files)} files and {len(evidence_list)} code references._"
        )

    return AskResponse(
        answer=answer_with_footnote,
        evidence=evidence_list,
        confidence=confidence,
        abstained=False,
        request_id=request_id,
    )


async def _generate_answer_ai(question: str, context: str) -> tuple[str, float]:
    """Call AI provider with a repo walkthrough persona and strict grounding instructions."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)

        messages = [
            {"role": "system", "content": REPO_WALKTHROUGH_SYSTEM_PROMPT.format(repo_analysis_context=context)},
            {"role": "user", "content": question},
        ]
        resp = await client.chat.completions.create(
            model=settings.COMPLETION_MODEL,
            messages=messages,
            max_tokens=1000,
            temperature=0.0,
        )
        answer = resp.choices[0].message.content or ""
        confidence = 0.3 if "don't have enough evidence" in answer.lower() else 0.85
        if "cannot be determined from static analysis alone" in answer.lower():
            confidence = 0.55
        return answer, confidence
    except Exception as exc:
        return f"AI provider unavailable: {exc}", 0.0


def _generate_answer_static(
    question: str,
    chunks: List[CodeChunk],
    repository_summary: Optional[str] = None,
) -> tuple[str, float]:
    """
    Static fallback when AI is unavailable.
    Summarizes what was found in retrieved chunks without inventing anything.
    """
    if not chunks and not repository_summary:
        return "No relevant code found for this question.", 0.0

    parts = ["Big picture:\n"]
    if repository_summary:
        parts.append(f"The repository summary suggests the main structure and relationships are:\n{repository_summary[:3500]}\n")
    for chunk in chunks[:4]:
        meta = chunk.metadata_ or {}
        file_path = meta.get("file_path", "unknown")
        symbol = meta.get("symbol_name", "")
        parts.append(f"• In {file_path}" + (f", {symbol}:" if symbol else ":"))
        preview = chunk.content[:200].replace("\n", " ").strip()
        parts.append(f"  {preview}")

    parts.append("\nEvidence basis: repository summary + relevant code chunks from the selected files. AI generation is unavailable; this is the grounded repository-only summary.")
    return "\n".join(parts), 0.65
