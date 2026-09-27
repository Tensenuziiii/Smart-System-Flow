"""Phase 2 verification gate: scan micrograd and produce fixture JSON."""
from __future__ import annotations

import json
import os
import sys
import time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.analyzer.scanner import scan_repository, get_repo_commit
import git


MICROGRAD_URL = "https://github.com/karpathy/micrograd"
MICROGRAD_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "micrograd_repo")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "fixtures", "micrograd_scan.json")


def clone_micrograd() -> str:
    """Clone micrograd if not already cloned."""
    if os.path.exists(MICROGRAD_DIR):
        print(f"[INFO] Repo already at {MICROGRAD_DIR}, skipping clone")
    else:
        print(f"[INFO] Cloning {MICROGRAD_URL} ...")
        git.Repo.clone_from(MICROGRAD_URL, MICROGRAD_DIR, depth=1)
        print(f"[INFO] Cloned to {MICROGRAD_DIR}")
    return MICROGRAD_DIR


def run_gate() -> None:
    os.makedirs(os.path.join(os.path.dirname(__file__), "fixtures"), exist_ok=True)

    repo_path = clone_micrograd()
    commit = get_repo_commit(repo_path) or "unknown"

    print(f"\n[GATE] Scanning {repo_path} (commit: {commit}) ...")
    t0 = time.time()
    result = scan_repository(repo_path, commit=commit)
    elapsed = time.time() - t0

    # ── Print file list ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"FILE LIST ({len(result.files)} files scanned in {elapsed:.2f}s)")
    print(f"{'='*60}")
    for f in result.files:
        print(f"  {f.path:<40} lang={f.language or '?':<12} hash={f.hash[:8]}  size={f.size_bytes:>7}B"
              + (f"  ERROR={f.parse_error}" if f.parse_error else ""))

    # ── Print symbols ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SYMBOL LIST ({len(result.symbols)} symbols)")
    print(f"{'='*60}")
    for s in result.symbols:
        print(f"  [{s.type:5}] {s.name:<40} {s.file_path}:{s.line_start}-{s.line_end}")

    # ── Import/call relationships ──────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"RELATIONSHIPS ({len(result.relationships)} total)")
    print(f"{'='*60}")
    imports = [r for r in result.relationships if r.rel_type == "imports"]
    calls = [r for r in result.relationships if r.rel_type == "calls"]
    print(f"  imports: {len(imports)}   calls: {len(calls)}")
    print("\n  Sample imports:")
    for r in imports[:10]:
        print(f"    {r.source_file} -> {r.target_name}")
    print("\n  Sample calls:")
    for r in calls[:10]:
        print(f"    {r.source_name} -> {r.target_name}  (line {r.evidence.get('line', '?')})")

    # ── Parse failure rate ────────────────────────────────────────────────
    total = result.total_files
    failed = result.parse_failures
    rate = (failed / total * 100) if total > 0 else 0
    print(f"\n{'='*60}")
    print(f"PARSE FAILURE RATE: {failed}/{total} = {rate:.1f}%")
    if failed > 0:
        for f in result.files:
            if f.parse_error:
                print(f"  FAILED: {f.path}: {f.parse_error}")
    print(f"{'='*60}")

    # ── Language distribution ─────────────────────────────────────────────
    print(f"\nLANGUAGES: {json.dumps(result.languages, indent=2)}")

    # ── Save fixture JSON ─────────────────────────────────────────────────
    fixture = {
        "commit": commit,
        "scan_time_seconds": round(elapsed, 3),
        "total_files": total,
        "parse_failures": failed,
        "parse_failure_rate_pct": round(rate, 2),
        "languages": result.languages,
        "files": [
            {
                "path": f.path,
                "language": f.language,
                "hash": f.hash,
                "size_bytes": f.size_bytes,
                "parse_error": f.parse_error,
            }
            for f in result.files
        ],
        "symbols": [
            {
                "name": s.name,
                "type": s.type,
                "file": s.file_path,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "docstring": s.docstring,
            }
            for s in result.symbols
        ],
        "relationships": [
            {
                "source": r.source_name,
                "source_file": r.source_file,
                "target": r.target_name,
                "type": r.rel_type,
                "evidence": r.evidence,
                "confidence": r.confidence,
            }
            for r in result.relationships
        ],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fp:
        json.dump(fixture, fp, indent=2)

    print(f"\n[GATE] Fixture saved to: {OUTPUT_FILE}")
    print(f"[GATE] Phase 2 DONE ✓ (parse failures: {failed}/{total})")

    if rate > 5:
        print(f"[WARN] Parse failure rate {rate:.1f}% exceeds 5% threshold!")
        sys.exit(1)


if __name__ == "__main__":
    run_gate()
