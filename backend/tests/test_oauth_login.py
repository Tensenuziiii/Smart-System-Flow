import os
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.v1.endpoints import routes
from app.core.security import verify_google_id_token, verify_microsoft_id_token


class OAuthLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_login_reports_missing_client_id(self):
        request = routes.OAuthTokenRequest(id_token="x" * 40)
        with patch.object(routes.settings, "GOOGLE_CLIENT_ID", None):
            with self.assertRaises(HTTPException) as raised:
                await routes.google_oauth(request)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("GOOGLE_CLIENT_ID", raised.exception.detail)

    async def test_microsoft_login_reports_missing_configuration(self):
        request = routes.OAuthTokenRequest(id_token="x" * 40)
        with patch.object(routes.settings, "MICROSOFT_CLIENT_ID", None):
            with patch.object(routes.settings, "MICROSOFT_TENANT_ID", None):
                with self.assertRaises(HTTPException) as raised:
                    await routes.microsoft_oauth(request)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("MICROSOFT_TENANT_ID", raised.exception.detail)

    async def test_google_rejects_malformed_provider_token(self):
        with self.assertRaises(HTTPException) as raised:
            await verify_google_id_token("not-a-jwt", "configured-client")
        self.assertEqual(raised.exception.status_code, 401)

    async def test_microsoft_rejects_malformed_provider_token(self):
        with self.assertRaises(HTTPException) as raised:
            await verify_microsoft_id_token("not-a-jwt", "configured-client", "common")
        self.assertEqual(raised.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
