"""All FastAPI route handlers — /v1/* endpoints."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    TokenData,
    create_access_token,
    get_current_tenant,
    verify_google_id_token,
    verify_microsoft_id_token,
)
from app.models.models import (
    ArchitectureEdge, ArchitectureNode, AuditLog, File, Repository,
    Relationship, StarterTask, Symbol, Trace,
)
from app.rag.rag_service import answer_question
from app.services.analysis_service import run_analysis_pipeline

settings = get_settings()
router = APIRouter(prefix="/v1")

# ── Error envelope helper ──────────────────────────────────────────────────────

def error_response(code: str, message: str, request_id: str = "") -> Dict:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


# ── Health endpoints (no auth) ─────────────────────────────────────────────────

@router.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "devonboard-api"}


@router.get("/ready", tags=["ops"])
async def ready(db: AsyncSession = Depends(get_db)):
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    status_val = "ready" if db_ok else "degraded"
    return {"status": status_val, "database": db_ok, "version": settings.APP_VERSION}


# ── Dev token endpoint (remove in prod) ───────────────────────────────────────

@router.post("/dev/token", tags=["dev"])
async def dev_token(tenant_id: str = "dev-tenant-001"):
    from app.core.security import issue_dev_token
    token = issue_dev_token(tenant_id)
    return {"access_token": token, "token_type": "bearer", "tenant_id": tenant_id}


class OAuthTokenRequest(BaseModel):
    id_token: str = Field(min_length=40, max_length=10000)


@router.post("/auth/oauth/google", tags=["auth"])
async def google_oauth(body: OAuthTokenRequest):
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth requires GOOGLE_CLIENT_ID in backend/.env")
    claims = await verify_google_id_token(body.id_token, settings.GOOGLE_CLIENT_ID)
    subject = claims["sub"]
    tenant_id = f"google:{subject}"
    return {
        "access_token": create_access_token(sub=f"google:{subject}", tenant_id=tenant_id),
        "token_type": "bearer",
        "tenant_id": tenant_id,
    }


@router.post("/auth/oauth/microsoft", tags=["auth"])
async def microsoft_oauth(body: OAuthTokenRequest):
    if not settings.MICROSOFT_CLIENT_ID or not settings.MICROSOFT_TENANT_ID:
        raise HTTPException(
            status_code=503,
            detail="Microsoft OAuth requires MICROSOFT_CLIENT_ID and MICROSOFT_TENANT_ID in backend/.env",
        )
    claims = await verify_microsoft_id_token(
        body.id_token,
        settings.MICROSOFT_CLIENT_ID,
        settings.MICROSOFT_TENANT_ID,
    )
    subject = claims["sub"]
    tenant_id = f"microsoft:{claims['tid']}:{subject}"
    return {
        "access_token": create_access_token(sub=f"microsoft:{subject}", tenant_id=tenant_id),
        "token_type": "bearer",
        "tenant_id": tenant_id,
    }


# ── Repository endpoints ───────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    repo_url: str
    name: Optional[str] = None


@router.post("/repositories/analyze", status_code=202, tags=["repositories"])
async def analyze_repository(
    body: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    request_id = str(uuid.uuid4())
    repo_url = body.repo_url
    name = body.name or repo_url.rstrip("/").split("/")[-1]

    # Idempotency: check for existing non-failed job
    stmt = select(Repository).where(
        Repository.tenant_id == tenant.tenant_id,
        Repository.url == repo_url,
        Repository.status.in_(["queued", "analyzing", "ready"]),
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        if existing.status == "ready":
            file_exists = (await db.execute(
                select(File.id).where(File.repository_id == existing.id).limit(1)
            )).first()
            if not file_exists:
                existing.status = "queued"
                existing.pipeline_stage = "queued"
                existing.error_message = None
                await db.commit()
                background_tasks.add_task(run_analysis_pipeline, existing.id, repo_url, tenant.tenant_id)
                return {
                    "job_id": existing.id,
                    "status": "queued",
                    "message": "Empty analysis is being retried",
                    "request_id": request_id,
                }
        return {
            "job_id": existing.id,
            "status": existing.status,
            "message": "Analysis already exists",
            "request_id": request_id,
        }

    repo_id = str(uuid.uuid4())
    repo = Repository(
        id=repo_id,
        tenant_id=tenant.tenant_id,
        url=repo_url,
        name=name,
        status="queued",
    )
    db.add(repo)
    db.add(AuditLog(
        tenant_id=tenant.tenant_id, actor=tenant.sub,
        action="analyze_repository", resource=repo_url,
    ))
    await db.commit()

    background_tasks.add_task(run_analysis_pipeline, repo_id, repo_url, tenant.tenant_id)

    return {
        "job_id": repo_id,
        "status": "queued",
        "message": "Analysis job queued",
        "request_id": request_id,
    }


@router.get("/repositories/{repo_id}", tags=["repositories"])
async def get_repository(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    # Count without materializing thousands of ORM rows on every poll.
    file_count = await db.scalar(select(func.count()).select_from(File).where(File.repository_id == repo_id)) or 0
    symbol_count = await db.scalar(select(func.count()).select_from(Symbol).where(Symbol.repository_id == repo_id)) or 0

    return {
        "id": repo.id,
        "tenant_id": repo.tenant_id,
        "url": repo.url,
        "name": repo.name,
        "commit": repo.commit,
        "status": repo.status,
        "pipeline_stage": repo.pipeline_stage,
        "partial_coverage": repo.partial_coverage,
        "languages": repo.languages,
        "file_count": file_count,
        "symbol_count": symbol_count,
        "architecture_mode": "grouped" if file_count > settings.ARCHITECTURE_CLUSTER_THRESHOLD else "flat",
        "architecture_threshold": settings.ARCHITECTURE_CLUSTER_THRESHOLD,
        "llm_configured": bool(settings.AI_AVAILABLE and settings.OPENAI_API_KEY),
        "error_message": repo.error_message,
        "created_at": repo.created_at.isoformat() if repo.created_at else None,
    }


@router.get("/repositories/{repo_id}/architecture", tags=["repositories"])
async def get_architecture(
    repo_id: str,
    cursor: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    nodes_stmt = select(ArchitectureNode).where(ArchitectureNode.repository_id == repo_id)
    nodes = (await db.execute(nodes_stmt)).scalars().all()

    edges_stmt = select(ArchitectureEdge).where(ArchitectureEdge.repository_id == repo_id)
    edges = (await db.execute(edges_stmt)).scalars().all()

    nodes_data = [
        {
            "id": n.id,
            "label": n.label,
            "type": n.type,
            "file_ids": n.file_ids,
            "confidence": n.confidence,
            "metadata": n.metadata_,
        }
        for n in nodes
    ]
    edges_data = [
        {
            "id": e.id,
            "source": e.source_node_id,
            "target": e.target_node_id,
            "relationship": e.rel_label,
            "evidence": e.evidence,
        }
        for e in edges
    ]

    return {
        "repository_id": repo_id,
        "nodes": nodes_data,
        "edges": edges_data,
        "meta": {
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
            "architecture_mode": "grouped" if len(nodes_data) > settings.ARCHITECTURE_CLUSTER_THRESHOLD else "flat",
            "cluster_threshold": settings.ARCHITECTURE_CLUSTER_THRESHOLD,
        },
    }


@router.get("/repositories/{repo_id}/files", tags=["repositories"])
async def get_files(
    repo_id: str,
    prefix: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    stmt = select(File).where(File.repository_id == repo_id)
    if prefix:
        stmt = stmt.where(File.path.startswith(prefix))
    all_files = (await db.execute(stmt)).scalars().all()

    total = len(all_files)
    start = (page - 1) * page_size
    paged = all_files[start:start + page_size]

    return {
        "files": [
            {
                "id": f.id,
                "path": f.path,
                "language": f.language,
                "hash": f.hash,
                "size_bytes": f.size_bytes,
                "parse_error": f.parse_error,
            }
            for f in paged
        ],
        "meta": {"total": total, "page": page, "page_size": page_size},
    }


@router.get("/repositories/{repo_id}/files/{file_id}/content", tags=["repositories"])
async def get_file_content(
    repo_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    """Returns raw file content for Monaco editor."""
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    file = await db.get(File, file_id)
    if not file or file.repository_id != repo_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "File not found"))

    return {
        "file_id": file_id,
        "path": file.path,
        "language": file.language,
        "content": file.content or "",
        "size_bytes": file.size_bytes,
    }


class AskRequest(BaseModel):
    question: str
    analysis_context: Optional[str] = None
    focus_file_path: Optional[str] = None


@router.post("/repositories/{repo_id}/ask", tags=["repositories"])
async def ask_question_endpoint(
    repo_id: str,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    request_id = str(uuid.uuid4())
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    if repo.status != "ready":
        raise HTTPException(status_code=409, detail=error_response("NOT_READY", f"Repository is {repo.status}"))

    result = await answer_question(
        db, repo_id, body.question,
        request_id=request_id,
        analysis_context=body.analysis_context,
        focus_file_path=body.focus_file_path,
    )

    return {
        "answer": result.answer,
        "evidence": [
            {
                "file_path": e.file_path,
                "symbol_name": e.symbol_name,
                "line_start": e.line_start,
                "line_end": e.line_end,
                "snippet": e.snippet,
                "confidence": e.confidence,
            }
            for e in result.evidence
        ],
        "confidence": result.confidence,
        "abstained": result.abstained,
        "request_id": request_id,
    }


class TraceRequest(BaseModel):
    query: str


_TRACE_TEST_PATH = re.compile(r"(?:^|/)(?:tests?|__tests__)(?:/|$)|(?:^|/)(?:test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.(?:py|js|jsx|ts|tsx|java|go)$", re.I)


def _is_trace_test_file(path: str) -> bool:
    return bool(_TRACE_TEST_PATH.search(path.replace("\\", "/")))


@router.post("/repositories/{repo_id}/trace", tags=["repositories"])
async def trace_feature(
    repo_id: str,
    body: TraceRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    """SSE stream of trace steps following a real call path."""
    request_id = str(uuid.uuid4())
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    if repo.status != "ready":
        raise HTTPException(status_code=409, detail=error_response("NOT_READY", f"Repository is {repo.status}"))

    # Resolve ranked symbols first. Test files are excluded from runtime traces
    # unless the query explicitly asks about tests or testing.
    query_words = [w.lower() for w in body.query.split() if len(w) > 2]
    includes_tests = bool(re.search(r"\btests?\b|\btesting\b|\btest[-_ ]", body.query, re.I))
    sym_stmt = select(Symbol, File).join(File, Symbol.file_id == File.id).where(Symbol.repository_id == repo_id)
    symbol_rows = (await db.execute(sym_stmt)).all()

    matched = []
    eligible_symbols = {}
    for sym, file in symbol_rows:
        if not includes_tests and _is_trace_test_file(file.path):
            continue
        eligible_symbols[sym.id] = {"symbol": sym, "file": file, "score": 0.0, "terms": []}
        name_lower = sym.name.lower()
        doc_lower = (sym.docstring or "").lower()
        matched_terms = {word for word in query_words if word in name_lower or word in doc_lower}
        name_terms = {word for word in query_words if word in name_lower}
        doc_terms = {word for word in query_words if word in doc_lower}
        if matched_terms:
            coverage = len(matched_terms) / max(len(query_words), 1)
            name_ratio = len(name_terms) / max(len(query_words), 1)
            doc_ratio = len(doc_terms) / max(len(query_words), 1)
            relevance = min(0.99, 0.55 * coverage + 0.3 * name_ratio + 0.15 * doc_ratio)
            matched.append({"symbol": sym, "file": file, "score": relevance, "terms": sorted(matched_terms)})
    matched.sort(key=lambda item: (-item["score"], item["symbol"].name.lower()))
    matched = matched[:10]

    relationships = (await db.execute(
        select(Relationship).where(Relationship.repository_id == repo_id)
    )).scalars().all()
    symbols_by_id = eligible_symbols
    outgoing = {}
    for relationship in relationships:
        if relationship.source_id in symbols_by_id and relationship.target_id in symbols_by_id:
            outgoing.setdefault(relationship.source_id, []).append(relationship)

    entry = matched[0] if matched else None
    causal_items = []
    if entry:
        queue = [(entry, None)]
        visited = set()
        while queue and len(causal_items) < 10:
            item, transition = queue.pop(0)
            symbol_id = item["symbol"].id
            if symbol_id in visited:
                continue
            visited.add(symbol_id)
            causal_items.append((item, transition))
            for relationship in outgoing.get(symbol_id, []):
                target = symbols_by_id.get(relationship.target_id)
                if target and target["symbol"].id not in visited:
                    target_with_path_score = dict(target)
                    target_with_path_score["path_score"] = item.get("path_score", item["score"]) * (relationship.confidence or 1.0)
                    queue.append((target_with_path_score, relationship))
    has_causal_path = len(causal_items) > 1

    async def event_stream():
        steps = []
        if not matched:
            step = {
                "step": 1, "type": "abstained",
                "message": f"No symbols found matching '{body.query}' in this repository",
                "confidence": 0.0,
            }
            yield f"data: {json.dumps(step)}\n\n"
            steps.append(step)
        elif has_causal_path:
            info = {
                "type": "trace_info", "mode": "causal",
                "message": "Showing a relationship-backed path from the most relevant symbol.",
            }
            yield f"data: {json.dumps(info)}\n\n"
            for i, (item, transition) in enumerate(causal_items):
                sym = item["symbol"]
                file = item["file"]
                step = {
                    "step": i + 1,
                    "type": "symbol_match",
                    "symbol": sym.name,
                    "symbol_type": sym.type,
                    "file": file.path,
                    "line_start": sym.line_start,
                    "line_end": sym.line_end,
                    "confidence": item.get("path_score", item["score"]),
                    "matched_terms": item["terms"],
                    "docstring": sym.docstring,
                }
                if transition:
                    step["transition"] = {
                        "type": transition.type,
                        "from": symbols_by_id[transition.source_id]["symbol"].name,
                        "evidence": transition.evidence or {},
                    }
                steps.append(step)
                yield f"data: {json.dumps(step)}\n\n"
                await asyncio.sleep(0.05)
        else:
            info = {
                "type": "trace_info", "mode": "relevance",
                "message": "No clear relationship-backed path was resolved. Showing related code sections by relevance, not a sequential execution path.",
            }
            yield f"data: {json.dumps(info)}\n\n"
            for i, item in enumerate(matched):
                sym = item["symbol"]
                file = item["file"]
                step = {
                    "step": i + 1,
                    "type": "related_symbol",
                    "symbol": sym.name,
                    "symbol_type": sym.type,
                    "file": file.path,
                    "line_start": sym.line_start,
                    "line_end": sym.line_end,
                    "confidence": item["score"],
                    "matched_terms": item["terms"],
                    "docstring": sym.docstring,
                }
                steps.append(step)
                yield f"data: {json.dumps(step)}\n\n"
                await asyncio.sleep(0.05)

        # Persist trace
        trace_status = "complete" if has_causal_path else ("partial" if matched else "abstained")
        trace = Trace(
            repository_id=repo_id,
            tenant_id=tenant.tenant_id,
            query=body.query,
            steps=steps,
            status=trace_status,
        )
        db.add(trace)
        await db.commit()

        yield f"data: {json.dumps({'type': 'done', 'trace_id': trace.id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/repositories/{repo_id}/setup", tags=["repositories"])
async def get_setup_guide(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    # Find config files in the repo
    config_stmt = select(File).where(
        File.repository_id == repo_id,
        File.path.in_(["requirements.txt", "setup.py", "pyproject.toml", "package.json",
                        "Makefile", "README.md", "docker-compose.yml", "Dockerfile"])
    )
    config_files = (await db.execute(config_stmt)).scalars().all()

    guide_sections = []
    for cf in config_files:
        if cf.content:
            guide_sections.append({
                "file": cf.path,
                "content_preview": cf.content[:2000],
                "language": cf.language,
            })

    return {
        "repository_id": repo_id,
        "name": repo.name,
        "languages": repo.languages,
        "config_files": guide_sections,
        "setup_steps": _generate_setup_steps(repo, config_files),
    }


def _generate_setup_steps(repo: Repository, config_files: list) -> List[Dict]:
    steps = []
    config_names = {cf.path for cf in config_files}
    langs = repo.languages or {}

    steps.append({"step": 1, "title": "Clone the repository", "command": f"git clone {repo.url}"})

    if "requirements.txt" in config_names:
        steps.append({"step": 2, "title": "Install Python dependencies",
                       "command": "pip install -r requirements.txt",
                       "evidence_file": "requirements.txt"})
    if "pyproject.toml" in config_names:
        steps.append({"step": 2, "title": "Install Python project",
                       "command": "pip install -e .",
                       "evidence_file": "pyproject.toml"})
    if "package.json" in config_names:
        steps.append({"step": 3, "title": "Install Node.js dependencies",
                       "command": "npm install",
                       "evidence_file": "package.json"})

    return steps


@router.post("/repositories/{repo_id}/validate", tags=["repositories"])
async def validate_setup(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    """Read-only validation of repo config files — never executes repo scripts."""
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    checks = []

    # Check: does requirements.txt exist?
    req_stmt = select(File).where(File.repository_id == repo_id, File.path == "requirements.txt")
    req_file = (await db.execute(req_stmt)).scalars().first()
    checks.append({
        "check": "requirements.txt exists",
        "passed": req_file is not None,
        "evidence": req_file.path if req_file else None,
    })

    # Check: any parse failures?
    all_files_stmt = select(File).where(File.repository_id == repo_id)
    all_files = (await db.execute(all_files_stmt)).scalars().all()
    failures = [f for f in all_files if f.parse_error]
    checks.append({
        "check": "parse failure rate < 5%",
        "passed": len(failures) / max(len(all_files), 1) < 0.05,
        "evidence": f"{len(failures)}/{len(all_files)} files failed",
    })

    return {"repository_id": repo_id, "checks": checks}


@router.get("/repositories/{repo_id}/tasks", tags=["repositories"])
async def get_starter_tasks(
    repo_id: str,
    difficulty: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant: TokenData = Depends(get_current_tenant),
):
    repo = await db.get(Repository, repo_id)
    if not repo or repo.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail=error_response("NOT_FOUND", "Repository not found"))

    stmt = select(StarterTask).where(StarterTask.repository_id == repo_id)
    if difficulty:
        stmt = stmt.where(StarterTask.difficulty == difficulty)
    tasks = (await db.execute(stmt)).scalars().all()

    # Generate tasks dynamically if none stored
    if not tasks:
        tasks = await _generate_starter_tasks(db, repo_id, tenant.tenant_id)

    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "difficulty": t.difficulty,
                "files": t.files,
                "rationale": t.rationale,
                "acceptance_criteria": t.acceptance_criteria,
            }
            for t in tasks
        ],
        "meta": {"total": len(tasks)},
    }


async def _generate_starter_tasks(db: AsyncSession, repo_id: str, tenant_id: str) -> List[StarterTask]:
    """Generate evidence-grounded starter tasks from actual symbols."""
    sym_stmt = select(Symbol).where(Symbol.repository_id == repo_id)
    symbols = (await db.execute(sym_stmt)).scalars().all()

    file_stmt = select(File).where(File.repository_id == repo_id, File.language == "python")
    py_files = (await db.execute(file_stmt)).scalars().all()

    tasks = []

    if py_files:
        # Beginner: add docstrings to undocumented functions
        undoc = [s for s in symbols if s.type == "fn" and not s.docstring][:3]
        if undoc:
            f_stmt = select(File).where(File.id == undoc[0].file_id)
            f = (await db.execute(f_stmt)).scalars().first()
            t = StarterTask(
                repository_id=repo_id, tenant_id=tenant_id,
                title="Add docstrings to undocumented functions",
                difficulty="beginner",
                files=[f.path] if f else [],
                rationale=f"Functions {', '.join(s.name for s in undoc)} lack docstrings",
                acceptance_criteria=[
                    "Each function has a docstring explaining its purpose",
                    "Docstrings follow the existing style in the codebase",
                ],
            )
            db.add(t)
            tasks.append(t)

    if symbols:
        # Intermediate: add type annotations
        fns = [s for s in symbols if s.type == "fn"][:2]
        if fns:
            f_ids = list({s.file_id for s in fns})
            f_stmt = select(File).where(File.id.in_(f_ids))
            files = (await db.execute(f_stmt)).scalars().all()
            t = StarterTask(
                repository_id=repo_id, tenant_id=tenant_id,
                title="Add type annotations to core functions",
                difficulty="intermediate",
                files=[f.path for f in files],
                rationale=f"Functions {', '.join(s.name for s in fns)} lack type hints",
                acceptance_criteria=[
                    "All parameters and return values have type annotations",
                    "mypy passes without errors on annotated functions",
                ],
            )
            db.add(t)
            tasks.append(t)

    await db.commit()
    return tasks
