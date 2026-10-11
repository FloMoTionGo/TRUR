"""Web hardening: body cap, security headers, generic 500s, the JSON/Origin check, rate limits (S7, S12, S2, S4).

The middleware is pure ASGI rather than BaseHTTPMiddleware, which would wrap streaming responses
in an extra task and memory stream (docs/architecture.md §8 item 22).
"""

import json
import logging
import math
import threading
from collections import deque

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from trur.clock import Clock
from trur.config import Settings
from trur.errors import ApiError

log = logging.getLogger("trur")

CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
HSTS = "max-age=31536000; includeSubDomains"


async def send_error(send: Send, status: int, code: str, message: str) -> None:
    """Send the error envelope straight from middleware, where no exception handler can run."""
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


class BodySizeLimit:
    """413 for a body over max_bytes: by Content-Length up front, or while a chunked body streams in."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            await send_error(send, 413, "payload_too_large", "The request body is too large.")
            return

        received = 0
        too_large = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            # Once the cap is hit, whatever the app makes of the aborted read is dropped for the 413.
            nonlocal started
            if too_large:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            pass
        except Exception:
            if not too_large:
                raise
        if too_large and not started:
            await send_error(send, 413, "payload_too_large", "The request body is too large.")


class InternalErrors:
    """Any unhandled exception → a generic 500 envelope. The details go to the log, never the client (S12)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            log.exception("unhandled error on %s %s", scope["method"], scope["path"])
            if started:
                raise
            await send_error(send, 500, "internal_error", "Something went wrong.")


class SecurityHeaders:
    """CSP, nosniff, no referrer and optional HSTS on every response; no-store on the API (S12)."""

    def __init__(self, app: ASGIApp, hsts: bool) -> None:
        self.app = app
        self.headers = [
            (b"content-security-policy", CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
        ]
        if hsts:
            self.headers.append((b"strict-transport-security", HSTS.encode()))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        added = list(self.headers)
        if scope["path"].startswith("/api/"):
            added.append((b"cache-control", b"no-store"))
        names = {name for name, _ in added}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message = {**message, "headers": kept + added}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def check_post(request: Request, s: Settings) -> None:
    """Every POST: a JSON body (415) from an allowed Origin (403, also when Origin is missing). CSRF defence (S2)."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ApiError(415, "unsupported_media_type", "Send the request body as application/json.")
    if request.headers.get("origin") not in s.allowed_origins:
        raise ApiError(403, "bad_origin", "This request must come from the game's own page.")


class RateLimiter:
    """In-memory sliding window per key. One process only (--workers 1); time comes from the Clock.

    Only allowed requests count, so a client that keeps getting 429 isn't locked out for longer.
    """

    SWEEP_EVERY = 1000  # checks between drops of idle keys, so old IPs don't pile up in memory

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._hits: dict[str, tuple[float, deque[float]]] = {}  # key → (window, hit times)
        self._lock = threading.Lock()
        self._checks = 0

    def check(self, key: str, limit: int, window_s: float) -> None:
        """Count one request for key, or raise 429 rate_limited if limit are already in the window. limit 0 = off."""
        if limit <= 0 or window_s <= 0:
            return
        now = self.clock.now().timestamp()
        with self._lock:
            self._checks += 1
            if self._checks % self.SWEEP_EVERY == 0:
                self._sweep(now)
            _, hits = self._hits.setdefault(key, (window_s, deque()))
            while hits and hits[0] <= now - window_s:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, math.ceil(hits[0] + window_s - now))
                raise ApiError(429, "rate_limited", "Too many requests. Please wait a moment.", retry_after=retry_after)
            hits.append(now)

    def _sweep(self, now: float) -> None:
        for key in [k for k, (window, hits) in self._hits.items() if not hits or hits[-1] <= now - window]:
            del self._hits[key]


def client_ip(request: Request, trust_fly: bool) -> str:
    """Fly-Client-IP when running behind Fly's proxy, else the socket peer. Used for rate limits only, never stored."""
    if trust_fly and (ip := request.headers.get("fly-client-ip")):
        return ip
    return request.client.host if request.client else "unknown"
