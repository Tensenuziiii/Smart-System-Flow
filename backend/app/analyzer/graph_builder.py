"""Architecture graph builder — turns ScanResult into ArchitectureNode/Edge rows."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.analyzer.scanner import ScanResult


@dataclass
class GraphNode:
    id: str
    label: str
    type: str  # frontend | api | service | db | external
    file_ids: List[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source_node_id: str
    target_node_id: str
    relationship: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchGraph:
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)


# ── Heuristic classifiers ──────────────────────────────────────────────────────

_DB_IMPORTS = {"sqlalchemy", "psycopg2", "asyncpg", "pymongo", "motor", "redis", "aioredis",
               "sqlite3", "peewee", "tortoise", "databases", "alembic"}
_API_PATTERNS = re.compile(r"(fastapi|flask|django|starlette|aiohttp|tornado|falcon|quart|sanic|bottle)", re.I)
_FRONTEND_PATTERNS = re.compile(r"\.(jsx|tsx|vue|svelte)$|components?/|pages?/|views?/|ui/|frontend/", re.I)
_TEST_PATTERNS = re.compile(r"(test_|_test\.|spec\.|\.spec\.|tests?/)", re.I)
_CONFIG_PATTERNS = re.compile(r"\.(env|cfg|ini|toml|yaml|yml|json|conf)$", re.I)
_EXTERNAL_IMPORTS = {"requests", "httpx", "aiohttp", "boto3", "stripe", "sendgrid", "twilio",
                     "openai", "anthropic", "google", "azure"}


def _classify_file(path: str, imports: Set[str]) -> str:
    """Return a node type for a file based on path and imports."""
    if _FRONTEND_PATTERNS.search(path):
        return "frontend"
    if _TEST_PATTERNS.search(path):
        return "service"  # tests are part of the service layer
    lower_imports = {i.lower().split(".")[0] for i in imports}
    if lower_imports & _DB_IMPORTS:
        return "db"
    if _API_PATTERNS.search(path) or lower_imports & {"fastapi", "flask", "django", "starlette"}:
        return "api"
    if lower_imports & _EXTERNAL_IMPORTS:
        return "external"
    return "service"


def build_architecture_graph(scan: ScanResult) -> ArchGraph:
    """
    Build an architecture graph from a ScanResult.

    Strategy:
    1. Each Python file becomes a node.
    2. Node type is inferred from path patterns + imports.
    3. Import relationships between files become edges.
    4. High-confidence call relationships also become edges.
    """
    graph = ArchGraph()
    node_by_path: Dict[str, GraphNode] = {}

    # ── Collect imports per file ───────────────────────────────────────────────
    imports_per_file: Dict[str, Set[str]] = {}
    for rel in scan.relationships:
        if rel.rel_type == "imports":
            imports_per_file.setdefault(rel.source_file, set()).add(rel.target_name)

    # ── Create one node per significant file ──────────────────────────────────
    all_files = [f for f in scan.files]

    for sf in all_files:
        node_type = _classify_file(sf.path, imports_per_file.get(sf.path, set()))
        # Use file basename (without ext) as label
        label = sf.path.replace("\\", "/").split("/")[-1]
        node = GraphNode(
            id=f"file::{sf.path}",
            label=label,
            type=node_type,
            file_ids=[sf.path],
            confidence=1.0 if sf.language == "python" else 0.8,
            metadata={"path": sf.path, "language": sf.language or "unknown", "size_bytes": sf.size_bytes},
        )
        node_by_path[sf.path] = node
        graph.nodes.append(node)

    # ── Build evidence-backed file relationship edges ─────────────────────────
    # Map module and symbol names to file paths across supported languages.
    path_by_module: Dict[str, str] = {}
    for sf in scan.files:
        mod = sf.path.replace("/", ".").replace("\\", ".")
        for suffix in (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go"):
            mod = mod.removesuffix(suffix)
        path_by_module[mod] = sf.path
        path_by_module[sf.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]] = sf.path

    symbol_paths: Dict[str, Set[str]] = {}
    for symbol in scan.symbols:
        symbol_paths.setdefault(symbol.name.split(".")[-1], set()).add(symbol.file_path)

    seen_edges: Set[Tuple[str, str, str]] = set()

    for rel in scan.relationships:
        src_path = rel.source_file
        if rel.target_file:
            target_path = rel.target_file
        elif rel.rel_type == "imports":
            target_mod = rel.target_name.replace("/", ".").removeprefix(".")
            target_path = path_by_module.get(target_mod) or path_by_module.get(target_mod.split(".")[-1])
        else:
            candidates = symbol_paths.get(rel.target_name.split(".")[-1], set())
            target_path = src_path if src_path in candidates else next(iter(candidates), None)
        if target_path and target_path != src_path:
            edge_key = (src_path, target_path, rel.rel_type)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                graph.edges.append(GraphEdge(
                    source_node_id=f"file::{src_path}",
                    target_node_id=f"file::{target_path}",
                    relationship=rel.rel_type,
                    evidence=rel.evidence,
                ))

    return graph
