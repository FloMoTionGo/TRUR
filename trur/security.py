"""Web hardening: body cap, security headers and generic 500s (S7, S12).

The middleware is pure ASGI rather than BaseHTTPMiddleware, which would wrap streaming responses
in an extra task and memory stream (docs/architecture.md §8 item 22).
"""

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

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

