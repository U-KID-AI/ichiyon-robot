"""Browser request protections. Internal APIs authenticate with their own secrets."""
import asyncio
import math
import os
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse


def origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return parsed.scheme, parsed.hostname.lower(), port
    except ValueError:
        return None


class AdminSecurityMiddleware:
    def __init__(self, app, base_url, max_body_bytes):
        self.app = app
        self.expected_origin = origin(base_url)
        if self.expected_origin is None:
            raise RuntimeError("ADMIN_BASE_URL must be an absolute HTTP(S) URL")
        self.max_body_bytes = max_body_bytes
        # Small process-local abuse guard; an edge proxy should also limit
        # aggregate traffic across workers and enforce connection timeouts.
        self.public_rate = max(1, int(os.getenv("ADMIN_PUBLIC_WRITE_RATE_PER_MINUTE", "20")))
        self.oauth_rate = max(1, int(os.getenv("ADMIN_OAUTH_RATE_PER_MINUTE", "30")))
        self.rate_windows = OrderedDict()
        self.aggregate_limits = {
            "public-write": max(1, int(os.getenv("ADMIN_PUBLIC_WRITE_TOTAL_PER_MINUTE", "200"))),
            "oauth-start": max(1, int(os.getenv("ADMIN_OAUTH_TOTAL_PER_MINUTE", "300"))),
        }
        self.aggregate_windows = {}
        self.body_idle_timeout = float(os.getenv("ADMIN_BODY_IDLE_TIMEOUT_SECONDS", "15"))
        self.body_total_timeout = float(os.getenv("ADMIN_BODY_TOTAL_TIMEOUT_SECONDS", "120"))
        if any(not math.isfinite(v) or not 0 < v <= 3600 for v in (self.body_idle_timeout, self.body_total_timeout)):
            raise RuntimeError("Admin body timeouts must be finite positive seconds, at most 3600")
        self.max_inflight_writes = int(os.getenv("ADMIN_MAX_INFLIGHT_WRITES", "8"))
        if not 1 <= self.max_inflight_writes <= 128:
            raise RuntimeError("ADMIN_MAX_INFLIGHT_WRITES must be between 1 and 128")
        self.inflight_writes = 0

    def rate_limited(self, scope):
        path, method = scope["path"], scope["method"]
        if path.startswith("/public") and method not in ("GET", "HEAD", "OPTIONS"):
            category, limit = "public-write", self.public_rate
        elif path.rstrip("/") == "/auth/discord" and method == "GET":
            category, limit = "oauth-start", self.oauth_rate
        else:
            return False
        now = time.monotonic()
        while self.rate_windows and now - next(iter(self.rate_windows.values()))[0] >= 60:
            self.rate_windows.popitem(last=False)
        client = scope.get("client")
        key = (category, client[0] if client else "unknown")
        started, count = self.rate_windows.get(key, (now, 0))
        if count >= limit:
            return True
        aggregate_started, aggregate_count = self.aggregate_windows.get(category, (now, 0))
        if now - aggregate_started >= 60:
            aggregate_started, aggregate_count = now, 0
        if aggregate_count >= self.aggregate_limits[category]:
            return True
        self.aggregate_windows[category] = (aggregate_started, aggregate_count + 1)
        if key not in self.rate_windows and len(self.rate_windows) >= 4096:
            self.rate_windows.popitem(last=False)
        self.rate_windows[key] = (started, count + 1)
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.setdefault("X-Content-Type-Options", "nosniff")
                response_headers.setdefault("X-Frame-Options", "DENY")
                response_headers.setdefault("Referrer-Policy", "same-origin")
                response_headers.setdefault("Cache-Control", "no-store")
            await send(message)

        # Use the ASGI path, never a URL assembled from an untrusted Host header.
        # These endpoints use bearer/header secrets, never the browser session.
        internal = scope["path"].startswith("/internal/")
        if not internal and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            supplied = headers.get("origin")
            if supplied is None:
                supplied = headers.get("referer", "")
            if origin(supplied) != self.expected_origin:
                return await PlainTextResponse("Invalid request origin", status_code=403)(scope, receive, secured_send)

        if self.rate_limited(scope):
            return await PlainTextResponse("Too many requests", status_code=429, headers={"Retry-After": "60"})(scope, receive, secured_send)

        # Bound both declared and chunked bodies before FastAPI parses uploads
        # or internal JSON. Pack archives go to the separate Control API, whose
        # authenticated streaming endpoint enforces its own limit.
        try:
            length = int(headers.get("content-length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await PlainTextResponse("Invalid content length", status_code=400)(scope, receive, secured_send)
        if length > self.max_body_bytes:
            return await PlainTextResponse("Request body too large", status_code=413)(scope, receive, secured_send)

        # Buffer at most the configured limit. Replaying the bounded body avoids
        # multipart parsers swallowing a receive exception as a generic 400.
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            if self.inflight_writes >= self.max_inflight_writes:
                return await PlainTextResponse("Server busy", status_code=503, headers={"Retry-After": "1"})(scope, receive, secured_send)
            self.inflight_writes += 1
            try:
                body = bytearray()
                deadline = asyncio.get_running_loop().time() + self.body_total_timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    try:
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        message = await asyncio.wait_for(receive(), timeout=min(self.body_idle_timeout, remaining))
                    except asyncio.TimeoutError:
                        return await PlainTextResponse("Request body timed out", status_code=408)(scope, receive, secured_send)
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > self.max_body_bytes:
                        return await PlainTextResponse("Request body too large", status_code=413)(scope, receive, secured_send)
                    if not message.get("more_body", False):
                        break
                delivered = False

                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                return await self.app(scope, replay, secured_send)
            finally:
                self.inflight_writes -= 1
        return await self.app(scope, receive, secured_send)


def admin_max_body_bytes():
    # Preserve the configurable audio limit, with multipart overhead.
    return max(8 * 1024 * 1024, int(os.getenv("AUDIO_ASSET_MAX_BYTES", "20971520"))) + 1024 * 1024
