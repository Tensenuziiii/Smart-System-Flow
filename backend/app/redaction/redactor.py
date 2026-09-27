"""Secret redaction filter — runs before any code chunk reaches the AI provider."""
from __future__ import annotations

import math
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

# ── Credential-bearing file basenames to NEVER index ──────────────────────────
EXCLUDED_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.development", ".env.staging",
    ".env.test", ".env.example",  # examples still excluded (may contain real pattern hints)
    "credentials.json", "credentials.yaml", "credentials.yml",
    "secrets.json", "secrets.yaml", "secrets.yml",
    ".netrc", ".pgpass", "id_rsa", "id_ed25519", "id_ecdsa",
}

EXCLUDED_EXTENSIONS = {".pem", ".key", ".pfx", ".p12", ".der", ".crt"}

# ── Regex patterns for typed secrets ─────────────────────────────────────────
_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("REDACTED_AWS_ACCESS_KEY",    re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b")),
    ("REDACTED_AWS_SECRET_KEY",    re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"]([A-Za-z0-9/+]{40})['\"]")),
    ("REDACTED_GITHUB_TOKEN",      re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{36,255}\b")),
    ("REDACTED_GOOGLE_API_KEY",    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("REDACTED_STRIPE_KEY",        re.compile(r"\b(sk|pk)_(test|live)_[0-9a-zA-Z]{24,}\b")),
    ("REDACTED_PRIVATE_KEY",       re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL)),
    ("REDACTED_JWT_SECRET",        re.compile(r"(?i)(jwt.{0,10}secret|secret.{0,10}jwt)\s*[:=]\s*['\"]?([A-Za-z0-9_\-+/=]{16,})['\"]?")),
    ("REDACTED_DB_PASSWORD",       re.compile(r"(?i)(password|passwd|db_pass|database_password)\s*[:=]\s*['\"]?([^\s'\"]{8,})['\"]?")),
    ("REDACTED_BEARER_TOKEN",      re.compile(r"(?i)bearer\s+([A-Za-z0-9\-_.~+/]+=*){20,}")),
    ("REDACTED_CONN_STRING",       re.compile(r"(?i)(mongodb|postgres|postgresql|mysql|redis)(\+\w+)?://[^:]+:[^@]+@[^\s\"']+")),
    ("REDACTED_SLACK_TOKEN",       re.compile(r"\b(xox[baprs]-[0-9A-Za-z\-]{10,})\b")),
    ("REDACTED_SENDGRID_KEY",      re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")),
    ("REDACTED_TWILIO_KEY",        re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("REDACTED_JWT_TOKEN",          re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("REDACTED_OPENAI_KEY",        re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b")),
    ("REDACTED_GENERIC_SECRET",    re.compile(r"(?i)(api_key|apikey|api.key|secret_key|access_token|auth_token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?")),
]

# ── Entropy detection ──────────────────────────────────────────────────────────
_HIGH_ENTROPY_RE = re.compile(r"""["']([A-Za-z0-9+/=_\-]{20,})["']""")
_ENTROPY_THRESHOLD = 4.5  # bits per character (Shannon entropy)
_MIN_ENTROPY_LENGTH = 20


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) / len(s) for c in set(s)}
    return -sum(p * math.log2(p) for p in freq.values())


@dataclass
class RedactionResult:
    original: str
    redacted: str
    redactions: List[str]  # list of placeholder types applied


def is_excluded_file(path: str) -> bool:
    """Return True if this file must never be indexed."""
    p = Path(path)
    name = p.name.lower()
    suffix = p.suffix.lower()
    return name in EXCLUDED_FILENAMES or suffix in EXCLUDED_EXTENSIONS


def redact(text: str) -> RedactionResult:
    """
    Apply all redaction rules to *text*.
    Returns a RedactionResult with the cleaned text and a list of what was
    replaced. The original text is also stored for audit purposes.
    """
    result = text
    applied: List[str] = []

    # 1. Pattern-based redaction
    for label, pattern in _PATTERNS:
        new, n = pattern.subn(f"<{label}>", result)
        if n > 0:
            result = new
            applied.extend([label] * n)

    # 2. Entropy-based redaction — catch anything pattern rules missed
    def _entropy_replace(m: re.Match[str]) -> str:
        candidate = m.group(1)
        if len(candidate) >= _MIN_ENTROPY_LENGTH and _shannon_entropy(candidate) >= _ENTROPY_THRESHOLD:
            applied.append("REDACTED_HIGH_ENTROPY_STRING")
            return f'"<REDACTED_HIGH_ENTROPY_STRING>"'
        return m.group(0)

    result = _HIGH_ENTROPY_RE.sub(_entropy_replace, result)

    return RedactionResult(original=text, redacted=result, redactions=applied)
