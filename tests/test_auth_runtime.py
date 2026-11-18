from __future__ import annotations

import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_service.auth_runtime import AccountBoundaryMiddleware, AuthConfig


SECRET = "test-only-runtime-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class AuthConfigurationTests(unittest.TestCase):
    def test_secret_and_session_limits(self):
        for secret in ("", "a" * 64, "123456789", "abCD012345" * 500):
            with self.subTest(secret_length=len(secret)), self.assertRaises(ValueError):
                AuthConfig(secret)
        for days in (0, 8, True, "7"):
            with self.subTest(days=days), self.assertRaises(ValueError):
                AuthConfig(SECRET, session_days=days)
        self.assertNotIn(SECRET, repr(AuthConfig(SECRET)))

    def test_public_origin_has_no_path_or_credentials(self):
        for origin in ("https://user:password@example.com", "https://example.com/api",
                       "https://example.com?token=secret", "https://example.com/#fragment",
                       "https://example.com:invalid", "https://exam ple.com", "file:///tmp"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                AuthConfig(SECRET, public_origin=origin)
        with self.assertRaises(ValueError):
            AuthConfig(SECRET, public_origin="http://example.com", cookie_secure=True)
        self.assertEqual(AuthConfig(SECRET, public_origin="https://example.com:8443",
                                   cookie_secure=True).session_days, 7)

    def test_production_fails_closed_on_insecure_configuration(self):
        settings = SimpleNamespace(environment="production", public_base_url=None)
        with patch.dict(os.environ, {"JWT_SECRET": SECRET, "PUBLIC_ORIGIN": "",
                                     "AUTH_COOKIE_SECURE": "true"}, clear=True):
            with self.assertRaisesRegex(ValueError, "PUBLIC_ORIGIN"):
                AuthConfig.from_environment(settings)
            os.environ["PUBLIC_ORIGIN"] = "https://rallymate.example"
            self.assertTrue(AuthConfig.from_environment(settings).cookie_secure)
            os.environ["AUTH_COOKIE_SECURE"] = "false"
            with self.assertRaisesRegex(ValueError, "Secure"):
                AuthConfig.from_environment(settings)


class AccountTransportTests(unittest.IsolatedAsyncioTestCase):
    async def exchange(self, path, chunks, *, ready=True, root_path="", method="POST"):
        received, sent, reached = iter(chunks), [], []

        async def receive():
            return next(received, {"type": "http.disconnect"})

        async def send(message):
            sent.append(message)

        async def inner(scope, receive, send):
            reached.append(await receive())
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = AccountBoundaryMiddleware(inner, runtime=SimpleNamespace(ready=ready))
        await middleware({"type": "http", "http_version": "1.1", "scheme": "https",
            "server": ("rallymate.example", 443), "client": ("127.0.0.1", 1000),
            "path": path, "root_path": root_path, "method": method,
            "query_string": b"", "headers": []}, receive, send)
        return sent, reached

    async def test_oversized_account_body_never_reaches_handler(self):
        for chunks in ([{"type": "http.request", "body": b"x" * 65537}],
                       [{"type": "http.request", "body": b"x" * 32768, "more_body": True},
                        {"type": "http.request", "body": b"x" * 32769}]):
            with self.subTest(chunks=len(chunks)):
                sent, reached = await self.exchange("/service/api/auth/register", chunks,
                                                    root_path="/service")
                self.assertEqual(sent[0]["status"], 413)
                self.assertEqual(reached, [])
                self.assertIn((b"cache-control", b"private, no-store"), sent[0]["headers"])

    async def test_account_body_replayed_exactly_once(self):
        sent, reached = await self.exchange("/api/admin/users/user-id", [
            {"type": "http.request", "body": b'{"status":', "more_body": True},
            {"type": "http.request", "body": b'"APPROVED"}'}], method="PATCH")
        self.assertEqual(sent[0]["status"], 204)
        self.assertEqual(json.loads(reached[0]["body"]), {"status": "APPROVED"})
        self.assertFalse(reached[0]["more_body"])

    async def test_startup_failure_rejects_private_request(self):
        for path in ("/v1/jobs", "/api/auth/login", "/api/admin/stats", "/api/records"):
            with self.subTest(path=path):
                sent, reached = await self.exchange(path, [], ready=False)
                self.assertEqual(sent[0]["status"], 503)
                self.assertEqual(reached, [])
