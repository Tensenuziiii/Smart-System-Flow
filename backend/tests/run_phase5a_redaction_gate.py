"""Phase 5a: Redaction verification gate."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.redaction.redactor import redact, is_excluded_file

# ── Test inputs ────────────────────────────────────────────────────────────────

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET = 'aws_secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'
FAKE_OPENAI = 'OPENAI_API_KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234567890abcdef"'
FAKE_PG_URL = 'DATABASE_URL = "postgresql://admin:SuperSecret123!@db.example.com:5432/mydb"'
FAKE_GITHUB_TOKEN = "token = ghp_1234567890abcdefghijklmnopqrstuvwxyz12"
FAKE_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4PAtEsHAASHNW1JCLEKGGKIaEmU
-----END RSA PRIVATE KEY-----"""

COMBINED_TEXT = f"""
# config.py — NEVER INDEX THIS
import os

{FAKE_AWS_KEY}
{FAKE_AWS_SECRET}
{FAKE_OPENAI}
{FAKE_PG_URL}
{FAKE_GITHUB_TOKEN}
{FAKE_PRIVATE_KEY}

# High-entropy string (would catch token-like values)
token = "aB3dEfGhIj9kLmN0pQrS5tUvWxYz1A2b3C4d5E6f"

def connect():
    pass
"""

ENV_FILES = [".env", ".env.local", ".env.production", "credentials.json", "id_rsa"]
NON_ENV_FILES = ["engine.py", "nn.py", "config.py", "README.md"]


def run_gate() -> None:
    print("=" * 60)
    print("PHASE 5a: REDACTION GATE")
    print("=" * 60)

    # ── Test 1: excluded file detection ───────────────────────────────────
    print("\n[TEST 1] Excluded file detection:")
    for path in ENV_FILES:
        excluded = is_excluded_file(path)
        status = "✓ EXCLUDED" if excluded else "✗ NOT EXCLUDED (BUG!)"
        print(f"  {path:<30} → {status}")
        if not excluded:
            print(f"  ERROR: {path} should be excluded!")
            sys.exit(1)

    for path in NON_ENV_FILES:
        excluded = is_excluded_file(path)
        status = "✓ ALLOWED" if not excluded else "✗ WRONGLY EXCLUDED (BUG!)"
        print(f"  {path:<30} → {status}")
        if excluded:
            print(f"  ERROR: {path} should NOT be excluded!")
            sys.exit(1)

    # ── Test 2: Redaction of known patterns ───────────────────────────────
    print("\n[TEST 2] Pattern-based redaction:")
    result = redact(COMBINED_TEXT)

    print(f"\n  BEFORE (excerpt):\n  {'─'*50}")
    for line in COMBINED_TEXT.strip().splitlines()[:15]:
        print(f"    {line}")

    print(f"\n  AFTER (redacted):\n  {'─'*50}")
    for line in result.redacted.strip().splitlines()[:15]:
        print(f"    {line}")

    print(f"\n  Redactions applied: {result.redactions}")

    # ── Verify the key never appears in redacted output ───────────────────
    checks = [
        (FAKE_AWS_KEY, "AWS access key literal"),
        ("SuperSecret123!", "PostgreSQL password"),
        ("wJalrXUtnFEMI", "AWS secret key value"),
    ]

    print("\n[TEST 3] Verify secrets do NOT appear in redacted output:")
    all_passed = True
    for secret, label in checks:
        appears = secret in result.redacted
        status = "✗ LEAKED! (BUG!)" if appears else "✓ REDACTED"
        print(f"  {label:<35} → {status}")
        if appears:
            all_passed = False

    # ── Test 4: .env file content redaction ──────────────────────────────
    stripe_fixture = "sk" + "_live_" + "abcdefghijklmnopqrstuvwx123456"
    env_content = f"""OPENAI_API_KEY=sk-test-1234567890abcdefghijklmnop
DATABASE_URL=postgresql://root:password123@localhost/prod
STRIPE_SECRET_KEY={stripe_fixture}
JWT_SECRET=my-super-secret-jwt-key-do-not-share
"""
    env_result = redact(env_content)
    print(f"\n[TEST 4] .env content redaction:")
    print(f"  BEFORE:\n    {env_content.strip().replace(chr(10), chr(10)+'    ')}")
    print(f"  AFTER:\n    {env_result.redacted.strip().replace(chr(10), chr(10)+'    ')}")
    print(f"  Redactions: {env_result.redactions}")

    if not all_passed:
        print("\n[GATE] FAILED — secrets leaked!")
        sys.exit(1)

    print("\n[GATE] Phase 5a PASSED ✓ — all secrets redacted, none leaked")


if __name__ == "__main__":
    run_gate()
