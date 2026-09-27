"""Repository scanner: clone, detect languages, parse AST via Tree-sitter."""
from __future__ import annotations

import ast
import hashlib
import logging
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from app.redaction.redactor import is_excluded_file

logger = logging.getLogger(__name__)

# Build Tree-sitter Python language
PY_LANGUAGE = Language(tspython.language())
_py_parser = Parser(PY_LANGUAGE)

# ── Language detection ─────────────────────────────────────────────────────────
EXTENSION_LANG: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "shell",
    ".bash": "shell",
    ".txt": "text",
    ".html": "html",
    ".css": "css",
}

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "dist", "build", ".next", "vendor",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear",
    ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".avi", ".mov",
    ".lock",  # package-lock, yarn.lock — too noisy
}


@dataclass
class ScannedFile:
    path: str
    language: Optional[str]
    hash: str
    size_bytes: int
    content: str
    parse_error: Optional[str] = None


@dataclass
class ExtractedSymbol:
    name: str
    type: str  # fn | class | var | route
    file_path: str
    line_start: int
    line_end: int
    docstring: Optional[str] = None


@dataclass
class ExtractedRelationship:
    source_name: str
    source_file: str
    target_name: str
    target_file: Optional[str]
    rel_type: str  # imports | calls | depends_on
    evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class ScanResult:
    repo_path: str
    commit: Optional[str]
    files: List[ScannedFile] = field(default_factory=list)
    symbols: List[ExtractedSymbol] = field(default_factory=list)
    relationships: List[ExtractedRelationship] = field(default_factory=list)
    languages: Dict[str, int] = field(default_factory=dict)
    parse_failures: int = 0
    total_files: int = 0
    error: Optional[str] = None


# ── File walking ───────────────────────────────────────────────────────────────

def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _cache_file(cache_dir: Optional[Path], file_hash: str) -> Optional[Path]:
    if not cache_dir:
        return None
    return cache_dir / f"{file_hash}.json"


def _load_cached_parse(cache_dir: Optional[Path], file_hash: str, path: str) -> Optional[Tuple[List[ExtractedSymbol], List[ExtractedRelationship], Optional[str]]]:
    cache_file = _cache_file(cache_dir, file_hash)
    if not cache_file or not cache_file.exists():
        return None
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        symbols = [ExtractedSymbol(**item) for item in cached.get("symbols", [])]
        relationships = []
        for item in cached.get("relationships", []):
            relationship = ExtractedRelationship(**item)
            relationship.source_file = path
            if relationship.evidence:
                relationship.evidence = {**relationship.evidence, "caller_file": path}
            relationships.append(relationship)
        for symbol in symbols:
            symbol.file_path = path
        return symbols, relationships, cached.get("parse_error")
    except (OSError, TypeError, ValueError, KeyError):
        return None


def _save_cached_parse(cache_dir: Optional[Path], file_hash: str, symbols: List[ExtractedSymbol], relationships: List[ExtractedRelationship], parse_error: Optional[str]) -> None:
    cache_file = _cache_file(cache_dir, file_hash)
    if not cache_file:
        return
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "symbols": [symbol.__dict__ for symbol in symbols],
            "relationships": [relationship.__dict__ for relationship in relationships],
            "parse_error": parse_error,
        }), encoding="utf-8")
    except OSError:
        logger.warning("Unable to write parse cache entry for %s", file_hash)


def walk_repo(repo_path: Path, max_files: int = 50_000, max_size: int = 2 * 1024 ** 3) -> List[Path]:
    """Recursively collect every non-excluded file in deterministic order."""
    files: List[Path] = []
    total_size = 0

    for root, dirs, filenames in os.walk(repo_path):
        # Prune skip dirs in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for fname in sorted(filenames):
            fp = Path(root) / fname
            if fp.suffix.lower() in SKIP_EXTENSIONS:
                continue
            if is_excluded_file(str(fp)):
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            total_size += size
            files.append(fp)
    logger.info(
        "Repository walk complete: files=%d bytes=%d skipped_dirs=%s",
        len(files), total_size, ",".join(sorted(SKIP_DIRS)),
    )
    return files


# ── Tree-sitter Python parsing ────────────────────────────────────────────────

def _get_node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _extract_docstring(node: Node, source: bytes) -> Optional[str]:
    """Extract first string literal from a function/class body as docstring."""
    for child in node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for inner in stmt.children:
                        if inner.type in ("string", "concatenated_string"):
                            return _get_node_text(inner, source).strip("\"'").strip()
    return None


def _parse_python_file(path: str, content: str) -> Tuple[List[ExtractedSymbol], List[ExtractedRelationship], Optional[str]]:
    """Parse a Python file with Tree-sitter and return symbols + relationships."""
    symbols: List[ExtractedSymbol] = []
    relationships: List[ExtractedRelationship] = []
    error: Optional[str] = None

    source = content.encode("utf-8")
    try:
        tree = _py_parser.parse(source)
    except Exception as exc:
        return symbols, relationships, f"tree-sitter parse error: {exc}"

    # Walk the tree for top-level and nested definitions
    def visit(node: Node, class_context: Optional[str] = None) -> None:
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                sym_name = _get_node_text(name_node, source)
                full_name = f"{class_context}.{sym_name}" if class_context else sym_name
                doc = _extract_docstring(node, source)
                symbols.append(ExtractedSymbol(
                    name=full_name,
                    type="fn",
                    file_path=path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    docstring=doc,
                ))
                # Look for calls inside function body
                _extract_calls(node, source, full_name, path, relationships)

        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = _get_node_text(name_node, source)
                doc = _extract_docstring(node, source)
                symbols.append(ExtractedSymbol(
                    name=cls_name,
                    type="class",
                    file_path=path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    docstring=doc,
                ))
                for child in node.children:
                    visit(child, class_context=cls_name)
                return  # already recursed into class body

        for child in node.children:
            if node.type not in ("function_definition", "class_definition"):
                visit(child, class_context=class_context)

    # Extract imports separately
    for node in tree.root_node.children:
        if node.type == "import_statement":
            names = [_get_node_text(c, source) for c in node.children if c.type in ("dotted_name", "aliased_import")]
            for n in names:
                relationships.append(ExtractedRelationship(
                    source_name="__module__",
                    source_file=path,
                    target_name=n.split(" as ")[0].strip(),
                    target_file=None,
                    rel_type="imports",
                    evidence={"line": node.start_point[0] + 1, "text": _get_node_text(node, source)},
                    confidence=1.0,
                ))
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module = _get_node_text(module_node, source) if module_node else "?"
            names_nodes = [c for c in node.children if c.type in ("dotted_name", "aliased_import") and c != module_node]
            for nn in names_nodes:
                relationships.append(ExtractedRelationship(
                    source_name="__module__",
                    source_file=path,
                    target_name=f"{module}.{_get_node_text(nn, source).split(' as ')[0].strip()}",
                    target_file=None,
                    rel_type="imports",
                    evidence={"line": node.start_point[0] + 1, "text": _get_node_text(node, source)},
                    confidence=1.0,
                ))

    visit(tree.root_node)
    return symbols, relationships, error


def _extract_calls(func_node: Node, source: bytes, caller_name: str, caller_file: str,
                   relationships: List[ExtractedRelationship]) -> None:
    """Walk function body and record all call_expression targets."""
    def _walk(node: Node) -> None:
        if node.type == "call":
            func_child = node.child_by_field_name("function")
            if func_child:
                callee = _get_node_text(func_child, source)
                # Only record simple names or attribute access (avoid huge lambda chains)
                if len(callee) < 100:
                    relationships.append(ExtractedRelationship(
                        source_name=caller_name,
                        source_file=caller_file,
                        target_name=callee,
                        target_file=None,
                        rel_type="calls",
                        evidence={
                            "line": node.start_point[0] + 1,
                            "caller_file": caller_file,
                        },
                        confidence=0.9,
                    ))
        for child in node.children:
            _walk(child)

    _walk(func_node)


# ── Generic fallback parser (Python ast) ─────────────────────────────────────

def _parse_python_fallback(path: str, content: str) -> Tuple[List[ExtractedSymbol], List[ExtractedRelationship], Optional[str]]:
    """Use stdlib ast as fallback if Tree-sitter fails."""
    symbols: List[ExtractedSymbol] = []
    relationships: List[ExtractedRelationship] = []
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as exc:
        return symbols, relationships, str(exc)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            symbols.append(ExtractedSymbol(
                name=node.name, type="fn", file_path=path,
                line_start=node.lineno, line_end=node.end_lineno or node.lineno,
                docstring=doc,
            ))
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node)
            symbols.append(ExtractedSymbol(
                name=node.name, type="class", file_path=path,
                line_start=node.lineno, line_end=node.end_lineno or node.lineno,
                docstring=doc,
            ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                relationships.append(ExtractedRelationship(
                    source_name="__module__", source_file=path,
                    target_name=alias.name, target_file=None,
                    rel_type="imports", evidence={"line": node.lineno}, confidence=1.0,
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                relationships.append(ExtractedRelationship(
                    source_name="__module__", source_file=path,
                    target_name=f"{module}.{alias.name}", target_file=None,
                    rel_type="imports", evidence={"line": node.lineno}, confidence=1.0,
                ))
    return symbols, relationships, None


_LANGUAGE_GRAMMAR_MODULES = {
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "java": "tree_sitter_java",
    "go": "tree_sitter_go",
}


def _validate_with_tree_sitter(language: str, content: str) -> Optional[str]:
    """Use an installed grammar when available; extraction remains evidence-based."""
    module_name = _LANGUAGE_GRAMMAR_MODULES.get(language)
    if not module_name:
        return None
    try:
        module = __import__(module_name)
        grammar = module.language_typescript() if language == "typescript" else module.language()
        parser = Parser(Language(grammar))
        tree = parser.parse(content.encode("utf-8"))
        if tree.root_node.has_error:
            return "tree-sitter syntax errors"
    except ImportError:
        # The core install only guarantees Python today; regex extraction keeps
        # supported files visible until an optional grammar package is present.
        return None
    except Exception as exc:
        return f"tree-sitter parse error: {exc}"
    return None


def _add_relationship(
    relationships: List[ExtractedRelationship], source: str, path: str,
    target: str, rel_type: str, line: int, text: str,
    target_file: Optional[str] = None,
) -> None:
    relationships.append(ExtractedRelationship(
        source_name=source, source_file=path, target_name=target,
        target_file=target_file, rel_type=rel_type,
        evidence={"line": line, "text": text[:500], "source_symbol": source},
        confidence=0.85,
    ))


def _parse_supported_source(path: str, language: str, content: str) -> Tuple[List[ExtractedSymbol], List[ExtractedRelationship], Optional[str]]:
    """Extract concrete symbols and relationships for the non-Python MVP languages."""
    symbols: List[ExtractedSymbol] = []
    relationships: List[ExtractedRelationship] = []
    syntax_error = _validate_with_tree_sitter(language, content)
    lines = content.splitlines()

    import_patterns = {
        "javascript": [r"\bimport\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]", r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"],
        "typescript": [r"\bimport\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]", r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"],
        "java": [r"^\s*import\s+([\w.]+)\s*;"],
        "go": [r"^\s*import\s+(?:\(?\s*)?[\"`]([^\"`]+)[\"`]"],
    }
    for line_no, line in enumerate(lines, 1):
        for pattern in import_patterns.get(language, []):
            for match in re.finditer(pattern, line):
                _add_relationship(relationships, "__module__", path, match.group(1), "imports", line_no, line)

    definition_patterns = {
        "javascript": r"\b(?:async\s+)?function\s+(\w+)|\bclass\s+(\w+)(?:\s+extends\s+([\w.]+))?|\b(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        "typescript": r"\b(?:async\s+)?function\s+(\w+)|\bclass\s+(\w+)(?:\s+extends\s+([\w.]+))?|\b(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        "java": r"\bclass\s+(\w+)(?:\s+extends\s+([\w.]+))?|\b(?:public|private|protected|static|final|synchronized|\s)+[\w<>\[\], ?]+\s+(\w+)\s*\([^;{]*\)\s*\{",
        "go": r"\bfunc\s+(?:\([^)]*\)\s*)?(\w+)\s*\(|\btype\s+(\w+)\s+struct\s*\{",
    }
    for line_no, line in enumerate(lines, 1):
        for match in re.finditer(definition_patterns.get(language, r"$^"), line):
            groups = [group for group in match.groups() if group]
            if not groups:
                continue
            name = groups[0]
            symbol_type = "class" if "class" in line or (language == "go" and "type" in line) else "fn"
            symbols.append(ExtractedSymbol(name=name, type=symbol_type, file_path=path, line_start=line_no, line_end=line_no))
            if symbol_type == "class" and len(groups) > 1 and groups[1]:
                _add_relationship(relationships, name, path, groups[1], "depends_on", line_no, line)

    route_pattern = re.compile(r"(?:@\w+\.(?:route|get|post|put|delete|patch)|\b(?:app|router)\.(?:get|post|put|delete|patch)|@(?:Get|Post|Put|Delete|Patch)Mapping|\b\w+\.(?:GET|POST|PUT|DELETE|PATCH))\s*\(?[^\n]*")
    current_symbol = "__module__"
    for line_no, line in enumerate(lines, 1):
        for route in route_pattern.findall(line):
            route_name = f"route:{route.strip()[:120]}"
            symbols.append(ExtractedSymbol(name=route_name, type="route", file_path=path, line_start=line_no, line_end=line_no))
            _add_relationship(relationships, route_name, path, route_name, "depends_on", line_no, line)
        for call in re.finditer(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(", line):
            callee = call.group(1)
            if callee in {"if", "for", "while", "switch", "catch", "func", "class", "return"}:
                continue
            _add_relationship(relationships, current_symbol, path, callee, "calls", line_no, line)

    return symbols, relationships, syntax_error


# ── Main scanner ───────────────────────────────────────────────────────────────

def scan_repository(
    repo_path: str,
    commit: Optional[str] = None,
    cache_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
) -> ScanResult:
    """
    Walk the repository, detect languages, parse every supported file,
    and return a ScanResult with files, symbols, and relationships.
    """
    root = Path(repo_path)
    result = ScanResult(repo_path=repo_path, commit=commit)

    file_paths = walk_repo(root)
    result.total_files = len(file_paths)
    parse_cache = Path(cache_dir) if cache_dir else None
    cached_files = 0

    for processed, fp in enumerate(file_paths, 1):
        rel_path = str(fp.relative_to(root)).replace("\\", "/")
        ext = fp.suffix.lower()
        lang = EXTENSION_LANG.get(ext)

        # Read content
        try:
            raw = fp.read_bytes()
            content = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            result.parse_failures += 1
            result.files.append(ScannedFile(
                path=rel_path, language=lang,
                hash="", size_bytes=0, content="",
                parse_error=str(exc),
            ))
            continue

        file_hash = _sha256(raw)
        size = len(raw)
        result.languages[lang or "unknown"] = result.languages.get(lang or "unknown", 0) + 1

        sf = ScannedFile(
            path=rel_path, language=lang, hash=file_hash,
            size_bytes=size, content=content,
        )

        # Parse every supported source file. Other indexed files remain available
        # to the file viewer and setup-guide stages without synthetic symbols.
        cached = _load_cached_parse(parse_cache, file_hash, rel_path) if lang in {"python", "javascript", "typescript", "java", "go"} else None
        if cached:
            syms, rels, err = cached
            cached_files += 1
        elif lang == "python":
            # Try Tree-sitter first
            syms, rels, err = _parse_python_file(rel_path, content)
            if err:
                # Fallback to stdlib ast
                syms, rels, err2 = _parse_python_fallback(rel_path, content)
                sf.parse_error = err2
                if err2:
                    result.parse_failures += 1
        elif lang in {"javascript", "typescript", "java", "go"}:
            syms, rels, err = _parse_supported_source(rel_path, lang, content)
            sf.parse_error = err
            if err:
                result.parse_failures += 1
        else:
            syms, rels, err = [], [], None

        result.symbols.extend(syms)
        result.relationships.extend(rels)

        if lang in {"python", "javascript", "typescript", "java", "go"} and not cached:
            _save_cached_parse(parse_cache, file_hash, syms, rels, sf.parse_error)

        result.files.append(sf)
        if progress_callback:
            progress_callback(processed, result.total_files, cached_files)

    logger.info(
        "Repository scan complete: traversed=%d parsed=%d parse_failures=%d languages=%s",
        result.total_files,
        sum(1 for scanned in result.files if scanned.language in {"python", "javascript", "typescript", "java", "go"}),
        result.parse_failures,
        result.languages,
    )
    return result


def get_repo_commit(repo_path: str) -> Optional[str]:
    """Get HEAD commit SHA if it's a git repo."""
    try:
        import subprocess
        r = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None
