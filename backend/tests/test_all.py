"""Unit tests with coverage tracking — Phase 9 gate."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRedactor(unittest.TestCase):
    """Tests for app.redaction.redactor"""

    def setUp(self):
        from app.redaction.redactor import redact, is_excluded_file
        self.redact = redact
        self.is_excluded = is_excluded_file

    def test_aws_access_key_redacted(self):
        r = self.redact("key: AKIAIOSFODNN7EXAMPLE")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", r.redacted)
        self.assertIn("REDACTED_AWS_ACCESS_KEY", r.redactions)

    def test_openai_key_redacted(self):
        r = self.redact('api_key = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"')
        self.assertNotIn("sk-proj-abcdefghijklmnopqrstuvwxyz", r.redacted)

    def test_github_token_redacted(self):
        r = self.redact("token = ghp_1234567890abcdefghijklmnopqrstuvwxyz12")
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz12", r.redacted)
        self.assertIn("REDACTED_GITHUB_TOKEN", r.redactions)

    def test_pg_connection_string_redacted(self):
        r = self.redact('db_url = "postgresql://admin:Secret123@db.example.com:5432/prod"')
        self.assertNotIn("Secret123", r.redacted)
        self.assertIn("REDACTED_CONN_STRING", r.redactions)

    def test_high_entropy_redacted(self):
        r = self.redact('"aB3dEfGhIj9kLmN0pQrS5tUvWxYz1A2b3C4d5E6f"')
        self.assertNotIn("aB3dEfGhIj9kLmN0pQrS5tUvWxYz", r.redacted)

    def test_normal_code_not_redacted(self):
        code = "def forward(x):\n    return x * 2"
        r = self.redact(code)
        self.assertEqual(r.redacted, code)
        self.assertEqual(r.redactions, [])

    def test_env_file_excluded(self):
        self.assertTrue(self.is_excluded(".env"))
        self.assertTrue(self.is_excluded(".env.production"))
        self.assertTrue(self.is_excluded("credentials.json"))
        self.assertTrue(self.is_excluded("id_rsa"))

    def test_source_file_not_excluded(self):
        self.assertFalse(self.is_excluded("engine.py"))
        self.assertFalse(self.is_excluded("nn.py"))
        self.assertFalse(self.is_excluded("README.md"))

    def test_private_key_block_redacted(self):
        r = self.redact("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----")
        self.assertNotIn("MIIe", r.redacted)
        self.assertIn("REDACTED_PRIVATE_KEY", r.redactions)

    def test_redaction_result_has_original(self):
        original = "AKIAIOSFODNN7EXAMPLE"
        r = self.redact(original)
        self.assertEqual(r.original, original)


class TestScanner(unittest.TestCase):
    """Tests for app.analyzer.scanner"""

    def setUp(self):
        self.fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        self.micrograd_dir = os.path.join(self.fixtures_dir, "micrograd_repo")

    def test_scan_engine_py(self):
        from app.analyzer.scanner import _parse_python_file
        engine_path = os.path.join(self.micrograd_dir, "micrograd", "engine.py")
        if not os.path.exists(engine_path):
            self.skipTest("micrograd_repo not cloned yet")
        content = open(engine_path, encoding="utf-8").read()
        syms, rels, err = _parse_python_file("micrograd/engine.py", content)
        self.assertIsNone(err)
        names = [s.name for s in syms]
        self.assertIn("Value", names)
        self.assertIn("Value.backward", names)
        self.assertIn("Value.__init__", names)

    def test_scan_nn_py(self):
        from app.analyzer.scanner import _parse_python_file
        nn_path = os.path.join(self.micrograd_dir, "micrograd", "nn.py")
        if not os.path.exists(nn_path):
            self.skipTest("micrograd_repo not cloned yet")
        content = open(nn_path, encoding="utf-8").read()
        syms, rels, err = _parse_python_file("micrograd/nn.py", content)
        self.assertIsNone(err)
        names = [s.name for s in syms]
        self.assertIn("Neuron", names)
        self.assertIn("Layer", names)
        self.assertIn("MLP", names)

    def test_full_scan_zero_parse_failures(self):
        from app.analyzer.scanner import scan_repository
        if not os.path.exists(self.micrograd_dir):
            self.skipTest("micrograd_repo not cloned yet")
        result = scan_repository(self.micrograd_dir)
        self.assertEqual(result.parse_failures, 0)
        self.assertEqual(result.total_files, 10)

    def test_walk_skips_git_dir(self):
        from app.analyzer.scanner import walk_repo
        from pathlib import Path
        if not os.path.exists(self.micrograd_dir):
            self.skipTest("micrograd_repo not cloned yet")
        files = walk_repo(Path(self.micrograd_dir))
        paths_str = [str(f) for f in files]
        # .git directory entries should be pruned, but .gitignore file is fine
        git_dirs = [p for p in paths_str if os.sep + ".git" + os.sep in p or p.endswith(os.sep + ".git")]
        self.assertEqual(git_dirs, [], f"Found .git directory files: {git_dirs}")

    def test_language_detection(self):
        from app.analyzer.scanner import EXTENSION_LANG
        self.assertEqual(EXTENSION_LANG[".py"], "python")
        self.assertEqual(EXTENSION_LANG[".ts"], "typescript")
        self.assertEqual(EXTENSION_LANG[".go"], "go")


class TestRAGService(unittest.TestCase):
    """Tests for out-of-scope detection."""

    def test_out_of_scope_capital(self):
        from app.rag.rag_service import _is_out_of_scope
        self.assertTrue(_is_out_of_scope("what is the capital of France"))

    def test_out_of_scope_weather(self):
        from app.rag.rag_service import _is_out_of_scope
        self.assertTrue(_is_out_of_scope("what is the weather in London"))

    def test_in_scope_code_question(self):
        from app.rag.rag_service import _is_out_of_scope
        self.assertFalse(_is_out_of_scope("what does the Value class do"))
        self.assertFalse(_is_out_of_scope("how does backward propagate gradients"))

    def test_cosine_similarity(self):
        from app.rag.rag_service import _cosine_similarity
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(_cosine_similarity(a, b), 1.0)
        c = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(_cosine_similarity(a, c), 0.0)

    def test_cosine_empty(self):
        from app.rag.rag_service import _cosine_similarity
        self.assertEqual(_cosine_similarity([], []), 0.0)

    def test_generate_answer_ai_uses_repo_walkthrough_persona(self):
        from app.rag import rag_service

        captured = {}

        class FakeChoice:
            class message:
                content = "Here is the repo walkthrough based on the code analysis."

        class FakeCompletions:
            async def create(self, **kwargs):
                captured["system_prompt"] = kwargs["messages"][0]["content"]
                captured["question"] = kwargs["messages"][1]["content"]
                return type("Response", (), {"choices": [FakeChoice()]})()

        class FakeAsyncOpenAI:
            def __init__(self, *args, **kwargs):
                pass

            @property
            def chat(self):
                return type("Chat", (), {"completions": FakeCompletions()})()

        with patch("openai.AsyncOpenAI", FakeAsyncOpenAI):
            answer, confidence = asyncio.run(
                rag_service._generate_answer_ai(
                    "What is the main architectural flow?",
                    "# File: app/main.py\n# File: app/api/v1/routes.py\nRepository context",
                )
            )

        self.assertIn("senior software engineer", captured["system_prompt"].lower())
        self.assertIn("provided repository analysis data", captured["system_prompt"].lower())
        self.assertIn("big picture", captured["system_prompt"].lower())
        self.assertIn("rather than guessing", captured["system_prompt"].lower())
        self.assertIn("real file paths", captured["system_prompt"].lower())
        self.assertIn("What is the main architectural flow?", captured["question"])
        self.assertIn("repo walkthrough", answer.lower())
        self.assertGreater(confidence, 0.0)

    def test_generate_answer_ai_abstains_when_repo_data_lacks_evidence(self):
        from app.rag import rag_service

        class FakeChoice:
            class message:
                content = "I don't have enough evidence in this repository to answer that confidently."

        class FakeCompletions:
            async def create(self, **kwargs):
                return type("Response", (), {"choices": [FakeChoice()]})()

        class FakeAsyncOpenAI:
            def __init__(self, *args, **kwargs):
                pass

            @property
            def chat(self):
                return type("Chat", (), {"completions": FakeCompletions()})()

        with patch("openai.AsyncOpenAI", FakeAsyncOpenAI):
            answer, confidence = asyncio.run(
                rag_service._generate_answer_ai(
                    "What is the capital of France?",
                    "# File: app/main.py\nprint('startup')",
                )
            )

        self.assertIn("don't have enough evidence in this repository", answer.lower())
        self.assertLess(confidence, 1.0)


class TestSecurity(unittest.TestCase):
    """Tests for JWT auth."""

    def test_token_roundtrip(self):
        from app.core.security import create_access_token, decode_token
        token = create_access_token(sub="testuser", tenant_id="tenant-xyz")
        data = decode_token(token)
        self.assertEqual(data.sub, "testuser")
        self.assertEqual(data.tenant_id, "tenant-xyz")

    def test_invalid_token_raises_401(self):
        from app.core.security import decode_token
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            decode_token("garbage.token.value")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_tenant_isolation(self):
        from app.core.security import create_access_token, decode_token
        t1 = create_access_token(sub="u1", tenant_id="tenant-A")
        t2 = create_access_token(sub="u2", tenant_id="tenant-B")
        d1 = decode_token(t1)
        d2 = decode_token(t2)
        self.assertNotEqual(d1.tenant_id, d2.tenant_id)


if __name__ == "__main__":
    # Run with coverage if available
    try:
        import coverage
        cov = coverage.Coverage(source=["app.redaction", "app.analyzer", "app.rag", "app.core"])
        cov.start()
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        cov.stop()
        cov.save()
        print("\n=== COVERAGE REPORT ===")
        cov.report(show_missing=True)
    except ImportError:
        print("Running without coverage (pip install coverage to enable)")
        unittest.main(verbosity=2)
