"""Offline security regressions: no live DB, Discord, or production requests."""
import asyncio
from base64 import b64encode
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_SESSION_SECRET", "offline-test-session-secret-32-characters")
os.environ.setdefault("ADMIN_BASE_URL", "http://testserver")

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

with patch("dotenv.load_dotenv", return_value=False):
    from admin import auth, main
from admin.security import AdminSecurityMiddleware


@contextmanager
def fake_connection():
    yield object()


class Permissions:
    def __init__(self, connection): pass
    def can_login_admin(self, user): return user in {"viewer", "admin"}
    def has_global_admin(self, user): return user == "admin"
    def get_admin_login_status(self, user): return {"can_login": user == "admin"}
    def set_last_login(self, user): pass


class SecurityChecks(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(auth, "get_connection", fake_connection),
                  patch.object(auth, "PermissionRepository", Permissions),
                  patch.object(main, "current_bot_instance_for_request", return_value={"bot_id": "ichiyon", "display_name": "test"}),
                  patch.dict(os.environ, {"ADMIN_ENABLE_LEGACY_JSON_PAGES": "true"})):
            p.start(); self.addCleanup(p.stop)
        self.client = TestClient(main.app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def cookie(self, data):
        encoded = b64encode(json.dumps(data).encode())
        self.client.cookies.set("session", TimestampSigner(auth.get_session_secret()).sign(encoded).decode())

    def test_production_rejects_weak_secrets_without_logging_them(self):
        for value in ("", "change_me", "a" * 31, " " * 40, "replace_with_a_long_random_development_secret", auth.DEV_SESSION_SECRET):
            with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_SESSION_SECRET": value}):
                with self.assertRaises(RuntimeError): auth.get_session_secret()
        with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_SESSION_SECRET": "offline-strong-test-secret-32-characters"}):
            self.assertTrue(auth.get_session_secret())

    def test_all_legacy_routes_have_global_admin_dependency(self):
        routes = [r for r in main.app.routes if main.is_legacy_json_path(getattr(r, "path", ""))]
        self.assertEqual(len(routes), 16)
        for route in routes:
            self.assertIn(auth.require_legacy_admin, [d.call for d in route.dependant.dependencies])

    def test_anonymous_and_viewer_cannot_read_or_write_global_data(self):
        for identity, expected in ((None, 401), ("viewer", 403), ("disabled", 401)):
            self.client.cookies.clear()
            if identity: self.cookie({"discord_user": {"user_id": identity}})
            for path in main.LEGACY_JSON_PATHS:
                self.assertEqual(self.client.get(path).status_code, expected)
                self.assertEqual(self.client.post(path, headers={"Origin": "http://testserver"}).status_code, expected)

    def test_admin_can_read_legacy_page(self):
        self.cookie({"discord_user": {"user_id": "admin"}})
        with patch.object(main, "load_quotes_data", return_value={"quotes": []}):
            self.assertEqual(self.client.get("/quotes").status_code, 200)

    def test_cross_origin_and_missing_origin_cannot_mutate(self):
        self.cookie({"discord_user": {"user_id": "admin"}})
        for headers in ({}, {"Origin": "null"}, {"Origin": "https://attacker.invalid"},
                        {"Origin": "http://testserver.attacker.invalid"},
                        {"Origin": "http://testserver:9999"},
                        {"Origin": "http://testserver:0"},
                        {"Origin": "http://testserver@attacker.invalid"},
                        {"Origin": "http://evil.invalid", "Referer": "http://testserver/"}):
            self.assertEqual(self.client.post("/logout", headers=headers).status_code, 403)

    def test_origin_and_referer_fallback_allow_real_forms(self):
        for headers in ({"Origin": "http://testserver"}, {"Referer": "http://testserver/me"}):
            self.cookie({"discord_user": {"user_id": "admin"}})
            response = self.client.post("/logout", headers=headers)
            self.assertEqual(response.status_code, 303)
            self.assertIn("session=null", response.headers["set-cookie"])

    def test_logout_get_does_not_mutate_session(self):
        self.cookie({"discord_user": {"user_id": "admin"}})
        self.assertEqual(self.client.get("/logout").status_code, 200)
        self.assertEqual(self.client.get("/me").status_code, 200)

    def test_forged_session_rejected(self):
        self.client.cookies.set("session", "forged-cookie")
        self.assertEqual(self.client.get("/quotes").status_code, 401)

    def test_expired_oauth_state_does_not_contact_discord(self):
        for issued in (None, time.time() - 601, time.time() + 60):
            self.cookie({auth.SESSION_STATE_KEY: "test-state", auth.SESSION_STATE_TIME_KEY: issued})
            with patch.object(auth, "fetch_discord_user", new_callable=AsyncMock) as fetch:
                response = self.client.get("/auth/discord/callback?code=test&state=test-state")
                self.assertEqual(response.headers["location"], "/login?error=invalid_state")
                fetch.assert_not_called()

    def test_internal_api_keeps_its_own_authentication(self):
        with patch.object(main.ai_task_internal_router, "prefix", "/internal/ai-tasks"):
            response = self.client.post("/internal/ai-tasks/claim", json={"runner_id": "test"})
            self.assertIn(response.status_code, (401, 422, 503))
            self.assertNotEqual(response.text, "Invalid request origin")

    def test_invalid_unicode_api_tokens_fail_closed(self):
        from admin import ai_tasks_internal, minecraft_internal
        from bot import config
        for attr, check, prefix in (("AI_TASK_RUNNER_API_TOKEN", ai_tasks_internal.require_runner_token, "Bearer "),
                                    ("MINECRAFT_BRIDGE_SECRET", minecraft_internal.require_minecraft_bridge_secret, "")):
            with patch.object(config, attr, "offline-secret"):
                for value in ("wrong", "不正なトークン"):
                    with self.assertRaises(Exception) as caught:
                        check(prefix + value)
                    self.assertEqual(caught.exception.status_code, 401)
                check(prefix + "offline-secret")

    def test_headers_and_session_attributes(self):
        self.cookie({"discord_user": {"user_id": "admin"}})
        response = self.client.get("/me")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        with patch.dict(os.environ, {"DISCORD_OAUTH_CLIENT_ID": "offline-id", "DISCORD_OAUTH_CLIENT_SECRET": "offline-secret"}):
            cookie = self.client.get("/auth/discord").headers["set-cookie"]
        self.assertIn("httponly", cookie.lower())
        self.assertIn("Max-Age=28800", cookie)
        self.assertIn("samesite=lax", cookie.lower())

    def test_bounded_declared_and_chunked_uploads(self):
        app = FastAPI()
        @app.post("/upload")
        async def upload(request: Request):
            return {"size": len(await request.body())}
        app.add_middleware(AdminSecurityMiddleware, base_url="http://testserver", max_body_bytes=8)
        with TestClient(app) as client:
            headers = {"Origin": "http://testserver"}
            self.assertEqual(client.post("/upload", content=b"x" * 9, headers=headers).status_code, 413)
            self.assertEqual(client.post("/upload", content=b"x" * 8, headers=headers).json(), {"size": 8})
        async def chunked():
            messages = iter([{"type":"http.request", "body":b"12345", "more_body":True},
                             {"type":"http.request", "body":b"6789", "more_body":False}])
            sent = []
            async def receive(): return next(messages)
            async def send(message): sent.append(message)
            await app({"type":"http", "method":"POST", "path":"/upload", "headers":[(b"origin",b"http://testserver")],
                       "scheme":"http", "query_string":b"", "server":("testserver",80), "client":("127.0.0.1",1), "root_path":""}, receive, send)
            self.assertEqual(sent[0]["status"], 413)
        asyncio.run(chunked())

    def test_public_write_rate_limit_expires_without_affecting_reads(self):
        app = FastAPI()
        @app.post("/public/submit")
        async def submit(): return {"ok": True}
        @app.get("/public/submit")
        async def read(): return {"ok": True}
        with patch.dict(os.environ, {"ADMIN_PUBLIC_WRITE_RATE_PER_MINUTE": "1"}):
            middleware = AdminSecurityMiddleware(app, "http://testserver", 32)
        with TestClient(middleware) as client:
            headers = {"Origin": "http://testserver"}
            self.assertEqual(client.post("/public/submit", headers=headers).status_code, 200)
            response = client.post("/public/submit", headers=headers)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["Retry-After"], "60")
            self.assertEqual(client.get("/public/submit").status_code, 200)
            with patch("admin.security.time.monotonic", return_value=time.monotonic() + 61):
                self.assertEqual(client.post("/public/submit", headers=headers).status_code, 200)

    def test_aggregate_budget_blocks_rotating_client_addresses(self):
        with patch.dict(os.environ, {"ADMIN_PUBLIC_WRITE_TOTAL_PER_MINUTE": "2"}):
            middleware = AdminSecurityMiddleware(FastAPI(), "http://testserver", 32)
        def scope(ip): return {"path": "/public/submit", "method": "POST", "client": (ip, 1)}
        self.assertFalse(middleware.rate_limited(scope("192.0.2.1")))
        self.assertFalse(middleware.rate_limited(scope("192.0.2.2")))
        self.assertTrue(middleware.rate_limited(scope("192.0.2.3")))
        with patch("admin.security.time.monotonic", return_value=time.monotonic() + 61):
            self.assertFalse(middleware.rate_limited(scope("192.0.2.3")))

    def test_slow_body_idle_and_total_timeouts_release_capacity(self):
        async def scenario(idle, total, interval, continuous):
            reached = []
            async def downstream(scope, receive, send): reached.append(True)
            middleware = AdminSecurityMiddleware(downstream, "http://testserver", 32)
            middleware.body_idle_timeout, middleware.body_total_timeout = idle, total
            sent = []
            async def receive():
                await asyncio.sleep(interval)
                return {"type": "http.request", "body": b"", "more_body": continuous}
            async def send(message): sent.append(message)
            scope = {"type": "http", "method": "POST", "path": "/upload", "headers": [(b"origin", b"http://testserver")]}
            await middleware(scope, receive, send)
            self.assertEqual(sent[0]["status"], 408)
            self.assertEqual(middleware.inflight_writes, 0)
            self.assertFalse(reached)
        asyncio.run(scenario(0.01, 1, 0.1, False))
        asyncio.run(scenario(1, 0.03, 0.005, True))

    def test_inflight_capacity_recovers_after_disconnect(self):
        async def scenario():
            middleware = AdminSecurityMiddleware(FastAPI(), "http://testserver", 32)
            middleware.max_inflight_writes = 1
            entered, release = asyncio.Event(), asyncio.Event()
            async def receive():
                entered.set()
                await release.wait()
                return {"type": "http.disconnect"}
            sent = []
            async def send(message): sent.append(message)
            scope = {"type": "http", "method": "POST", "path": "/upload", "headers": [(b"origin", b"http://testserver")]}
            first = asyncio.create_task(middleware(scope, receive, send))
            await entered.wait()
            await middleware(scope, receive, send)
            self.assertEqual(sent[0]["status"], 503)
            release.set()
            await first
            self.assertEqual(middleware.inflight_writes, 0)
        asyncio.run(scenario())

    def test_nonfinite_timeout_configuration_is_rejected(self):
        for value in ("nan", "inf", "0", "-1"):
            with patch.dict(os.environ, {"ADMIN_BODY_IDLE_TIMEOUT_SECONDS": value}):
                with self.assertRaises(RuntimeError):
                    AdminSecurityMiddleware(FastAPI(), "http://testserver", 32)


if __name__ == "__main__":
    unittest.main()
