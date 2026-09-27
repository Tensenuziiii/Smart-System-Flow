"""Analysis pipeline service: orchestrates scan → persist → graph → RAG indexing."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import git
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.analyzer.graph_builder import build_architecture_graph
from app.analyzer.scanner import ScanResult, get_repo_commit, scan_repository
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.models import (
    ArchitectureEdge, ArchitectureNode, CodeChunk, File, Relationship,
    Repository, Symbol,
)
from app.redaction.redactor import redact

settings = get_settings()


def _write_repository_summary(repo_id: str, scan: ScanResult, graph: Any) -> None:
    """Cache bounded repo-level context for grounded whole-repository questions."""
    paths = [file.path.replace("\\", "/") for file in scan.files]
    top_level_counts: Dict[str, int] = {}
    for path in paths:
        top_level = path.split("/", 1)[0]
        top_level_counts[top_level] = top_level_counts.get(top_level, 0) + 1

    path_by_node = {node.id: (node.metadata or {}).get("path", node.label) for node in graph.nodes}
    relationships = []
    for edge in graph.edges[:250]:
        relationships.append({
            "source": path_by_node.get(edge.source_node_id, edge.source_node_id),
            "target": path_by_node.get(edge.target_node_id, edge.target_node_id),
            "type": edge.relationship,
            "evidence": edge.evidence,
        })

    entry_points = [
        {"name": symbol.name, "file": symbol.file_path, "type": symbol.type,
         "lines": [symbol.line_start, symbol.line_end]}
        for symbol in scan.symbols
        if symbol.type == "route" or symbol.name.lower().split(".")[-1] in {"main", "app", "run", "start", "cli"}
    ][:100]
    summary = {
        "repo_id": repo_id,
        "total_files": len(scan.files),
        "languages": scan.languages,
        "top_level_modules": dict(sorted(top_level_counts.items(), key=lambda item: (-item[1], item[0]))[:100]),
        "entry_points": entry_points,
        "relationships": relationships,
        "symbol_count": len(scan.symbols),
        "relationship_count": len(scan.relationships),
    }
    summary_path = Path(settings.PARSE_CACHE_DIR) / "repositories" / f"{repo_id}.json"
    try:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
    except OSError:
        # The analysis remains valid if an optional cache write is unavailable.
        pass


async def _update_pipeline_progress(repo_id: str, processed: int, total: int, cached: int) -> None:
    async with AsyncSessionLocal() as progress_db:
        progress_repo = await progress_db.get(Repository, repo_id)
        if progress_repo and progress_repo.status == "analyzing":
            progress_repo.pipeline_stage = f"parsing:{processed}/{total}"
            await progress_db.commit()


async def clone_or_update_repo(repo_url: str, dest_dir: str) -> str:
    """Clone repo into dest_dir. Returns path to cloned dir."""
    loop = asyncio.get_event_loop()

    def _clone() -> str:
        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        git.Repo.clone_from(repo_url, dest_dir, depth=1)
        return dest_dir

    return await loop.run_in_executor(None, _clone)


async def run_analysis_pipeline(repo_id: str, repo_url: str, tenant_id: str) -> None:
    """
    Full analysis pipeline:
    1. Clone repo
    2. Scan + parse (Tree-sitter)
    3. Persist files/symbols/relationships
    4. Build architecture graph
    5. Chunk + prepare for RAG
    """
    dest_dir = os.path.join(settings.REPOS_DIR, repo_id)
    os.makedirs(settings.REPOS_DIR, exist_ok=True)

    async with AsyncSessionLocal() as db:
        repo = await db.get(Repository, repo_id)
        if not repo:
            return

        # ── Stage 1: Clone ─────────────────────────────────────────────────
        repo.status = "analyzing"
        repo.pipeline_stage = "cloning"
        await db.commit()

        try:
            await asyncio.wait_for(clone_or_update_repo(repo_url, dest_dir), timeout=settings.ANALYSIS_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            repo.status = "failed"
            repo.error_message = f"Analysis timed out while cloning after {settings.ANALYSIS_TIMEOUT_SECONDS} seconds."
            await db.commit()
            return
        except Exception as exc:
            repo.status = "failed"
            repo.error_message = f"Clone failed: {exc}"
            await db.commit()
            return

        commit = get_repo_commit(dest_dir) or "unknown"
        repo.commit = commit
        repo.pipeline_stage = "scanning"
        await db.commit()

        # ── Stage 2: Scan ──────────────────────────────────────────────────
        loop = asyncio.get_event_loop()
        cache_key = hashlib.sha256(repo_url.encode("utf-8")).hexdigest()
        cache_dir = os.path.join(settings.PARSE_CACHE_DIR, cache_key)
        progress_futures = []
        last_reported = [0]

        def report_progress(processed: int, total: int, cached: int) -> None:
            if processed != total and processed - last_reported[0] < settings.PARSE_PROGRESS_INTERVAL:
                return
            last_reported[0] = processed
            progress_futures.append(asyncio.run_coroutine_threadsafe(
                _update_pipeline_progress(repo_id, processed, total, cached), loop
            ))

        scan_future = loop.run_in_executor(
            None, scan_repository, dest_dir, commit, cache_dir, report_progress
        )
        try:
            scan: ScanResult = await asyncio.wait_for(scan_future, timeout=settings.ANALYSIS_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            repo.status = "failed"
            repo.pipeline_stage = "scanning"
            repo.error_message = f"Analysis timed out while parsing after {settings.ANALYSIS_TIMEOUT_SECONDS} seconds."
            await db.commit()
            return
        if progress_futures:
            await asyncio.gather(*(asyncio.wrap_future(future) for future in progress_futures))

        if scan.total_files == 0:
            repo.status = "failed"
            repo.pipeline_stage = "scanning"
            repo.error_message = "Repository contains no indexable files or source files."
            await db.commit()
            return

        repo.languages = scan.languages
        repo.pipeline_stage = "persisting"
        if scan.total_files > 0 and scan.parse_failures / scan.total_files > 0.05:
            repo.partial_coverage = True
        await db.commit()

        # ── Stage 3: Persist files + symbols ──────────────────────────────
        file_id_by_path: Dict[str, str] = {}
        symbol_id_by_name_file: Dict[str, str] = {}

        for sf in scan.files:
            file_id = str(uuid.uuid4())
            file_id_by_path[sf.path] = file_id
            db_file = File(
                id=file_id,
                repository_id=repo_id,
                path=sf.path,
                hash=sf.hash,
                language=sf.language,
                size_bytes=sf.size_bytes,
                parse_error=sf.parse_error,
                content=sf.content[:50_000] if sf.content else None,  # cap stored content
            )
            db.add(db_file)

        await db.flush()

        for sym in scan.symbols:
            fid = file_id_by_path.get(sym.file_path)
            if not fid:
                continue
            sym_id = str(uuid.uuid4())
            symbol_id_by_name_file[f"{sym.name}::{sym.file_path}"] = sym_id
            db_sym = Symbol(
                id=sym_id,
                file_id=fid,
                repository_id=repo_id,
                name=sym.name,
                type=sym.type,
                line_start=sym.line_start,
                line_end=sym.line_end,
                docstring=sym.docstring,
            )
            db.add(db_sym)

        await db.flush()

        # ── Stage 4: Persist relationships ────────────────────────────────
        for rel in scan.relationships:
            src_sym_id = symbol_id_by_name_file.get(f"{rel.source_name}::{rel.source_file}")
            target_file = rel.target_file or rel.source_file
            tgt_key = f"{rel.target_name}::{target_file}"
            tgt_sym_id = symbol_id_by_name_file.get(tgt_key) or f"external::{rel.target_name}"
            db_rel = Relationship(
                repository_id=repo_id,
                source_id=src_sym_id or f"external::{rel.source_name}",
                target_id=tgt_sym_id,
                type=rel.rel_type,
                evidence=rel.evidence,
                confidence=rel.confidence,
            )
            db.add(db_rel)

        await db.flush()

        # ── Stage 5: Build + persist architecture graph ────────────────────
        repo.pipeline_stage = "building_graph"
        await db.commit()

        try:
            graph = await asyncio.wait_for(
                loop.run_in_executor(None, build_architecture_graph, scan),
                timeout=settings.ANALYSIS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            repo.status = "failed"
            repo.pipeline_stage = "building_graph"
            repo.error_message = f"Analysis timed out while building the graph after {settings.ANALYSIS_TIMEOUT_SECONDS} seconds."
            await db.commit()
            return
        _write_repository_summary(repo_id, scan, graph)
        node_id_by_file: Dict[str, str] = {}

        for gn in graph.nodes:
            node_db_id = str(uuid.uuid4())
            node_id_by_file[gn.id] = node_db_id
            file_ids = [file_id_by_path.get(p, p) for p in gn.file_ids]
            db_node = ArchitectureNode(
                id=node_db_id,
                repository_id=repo_id,
                label=gn.label,
                type=gn.type,
                file_ids=file_ids,
                confidence=gn.confidence,
                metadata_=gn.metadata,
            )
            db.add(db_node)

        await db.flush()

        for ge in graph.edges:
            src_id = node_id_by_file.get(ge.source_node_id)
            tgt_id = node_id_by_file.get(ge.target_node_id)
            if src_id and tgt_id:
                db_edge = ArchitectureEdge(
                    repository_id=repo_id,
                    source_node_id=src_id,
                    target_node_id=tgt_id,
                    rel_label=ge.relationship,
                    evidence=ge.evidence,
                )
                db.add(db_edge)

        # ── Stage 6: Chunk code for RAG ────────────────────────────────────
        repo.pipeline_stage = "chunking"
        await db.flush()

        for sf in scan.files:
            if not sf.content or sf.language != "python":
                continue
            fid = file_id_by_path.get(sf.path)
            if not fid:
                continue
            # Symbol-boundary chunking: one chunk per symbol
            file_syms = [s for s in scan.symbols if s.file_path == sf.path]
            if file_syms:
                for sym in file_syms:
                    lines = sf.content.splitlines()
                    start = max(0, sym.line_start - 1)
                    end = min(len(lines), sym.line_end)
                    chunk_text = "\n".join(lines[start:end])
                    r = redact(chunk_text)
                    sym_id = symbol_id_by_name_file.get(f"{sym.name}::{sym.file_path}")
                    chunk = CodeChunk(
                        repository_id=repo_id,
                        file_id=fid,
                        symbol_id=sym_id,
                        content=r.redacted,
                        token_count=len(chunk_text.split()),
                        metadata_={
                            "file_path": sf.path,
                            "symbol_name": sym.name,
                            "symbol_type": sym.type,
                            "line_start": sym.line_start,
                            "line_end": sym.line_end,
                            "redactions": r.redactions,
                        },
                    )
                    db.add(chunk)
            else:
                # No symbols found — chunk whole file
                r = redact(sf.content[:4000])
                chunk = CodeChunk(
                    repository_id=repo_id,
                    file_id=fid,
                    content=r.redacted,
                    token_count=len(sf.content.split()),
                    metadata_={"file_path": sf.path, "redactions": r.redactions},
                )
                db.add(chunk)

        repo.status = "ready"
        repo.pipeline_stage = "ready"
        await db.commit()
