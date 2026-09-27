"""Security and isolation tests — Phase 8 gate."""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.redaction.redactor import redact, is_excluded_file
from app.core.security import create_access_token, decode_token
from fastapi import HTTPException


def test_redaction_bypass_attempts() -> None:
    """
    Attempts to bypass redaction via common obfuscation tricks.
    The redactor must catch these or the test fails.
    """
    print("\n[TEST] Redaction bypass attempts:")

    # 1. Key split across concatenation (static analysis can't catch this at runtime,
    #    but we verify the literal forms are caught)
    cases = [
        ("Direct AWS key", "AKIAIOSFODNN7EXAMPLE",
         lambda r: "AKIAIOSFODNN7EXAMPLE" not in r.redacted),
        ("OpenAI key", 'api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890abc"',
         lambda r: "sk-abcdefghijklmnopqrstuvwxyz" not in r.redacted),
        ("Postgres connection string",
         'db = "postgresql://user:Password1!@host.rds.amazonaws.com:5432/mydb"',
         lambda r: "Password1!" not in r.redacted),
        ("GitHub PAT", "token = ghp_ZxCvBnMqWeRtYuIoPlKjHgFdSaQwErTy1234",
         lambda r: "ghp_ZxCvBnMqWeRtYuIoPlKjHgFdSaQwErTy1234" not in r.redacted),
        ("High-entropy string (JWT-like)",
         '"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"',
         lambda r: "eyJhbGciOiJIUzI1NiJ9" not in r.redacted),
    ]

    all_passed = True
    for label, text, check_fn in cases:
        r = redact(text)
        passed = check_fn(r)
        status = "PASS" if passed else "FAIL (LEAKED!)"
        print(f"  {label:<45}: {status}")
        if not passed:
            print(f"    Leaked in: {r.redacted}")
            all_passed = False

    assert all_passed, "Redaction bypass test FAILED — secrets leaked!"
    print("  => All bypass checks PASSED")


def test_tenant_isolation() -> None:
    """
    Verify that tokens from different tenants cannot be confused.
    """
    print("\n[TEST] Tenant isolation:")

    token_a = create_access_token(sub="user-a", tenant_id="tenant-aaa")
    token_b = create_access_token(sub="user-b", tenant_id="tenant-bbb")

    data_a = decode_token(token_a)
    data_b = decode_token(token_b)

    assert data_a.tenant_id == "tenant-aaa", f"Wrong tenant: {data_a.tenant_id}"
    assert data_b.tenant_id == "tenant-bbb", f"Wrong tenant: {data_b.tenant_id}"
    assert data_a.tenant_id != data_b.tenant_id, "Tenant IDs should differ!"
    print("  tenant-aaa token -> decoded tenant_id=tenant-aaa  PASS")
    print("  tenant-bbb token -> decoded tenant_id=tenant-bbb  PASS")
    print("  Cross-tenant mismatch confirmed  PASS")

    # Invalid token must raise
    try:
        decode_token("not.a.valid.jwt")
        print("  FAIL: invalid token should have raised")
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 401
        print("  Invalid token -> 401 Unauthorized  PASS")


def test_excluded_files_never_indexed() -> None:
    """Credential-bearing files must never be indexed."""
    print("\n[TEST] File exclusion (sandbox escape simulation):")

    must_exclude = [
        ".env", ".env.production", "credentials.json", "id_rsa", ".netrc",
        "secrets.yaml", "private.key", "server.pem",
    ]
    must_allow = ["engine.py", "nn.py", "setup.py", "README.md", "test_engine.py"]

    for path in must_exclude:
        assert is_excluded_file(path), f"FAILED: {path} should be excluded!"
        print(f"  {path:<35} -> EXCLUDED  PASS")

    for path in must_allow:
        assert not is_excluded_file(path), f"FAILED: {path} should NOT be excluded!"
        print(f"  {path:<35} -> ALLOWED   PASS")


def run_gate() -> None:
    print("=" * 60)
    print("PHASE 8: SECURITY HARDENING GATE")
    print("=" * 60)

    test_redaction_bypass_attempts()
    test_tenant_isolation()
    test_excluded_files_never_indexed()

    print("\n" + "=" * 60)
    print("Phase 8 PASSED ✓ — All security checks passed")
    print("=" * 60)


if __name__ == "__main__":
    run_gate()
