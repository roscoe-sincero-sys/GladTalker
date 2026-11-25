"""Request guard, WebSocket tickets, and credential redaction for the Studio engine.

The engine reads local images, writes files, and starts downloads, so every HTTP request is
screened by a pure ASGI guard *before* FastAPI parses its body:

* **Origin**: a browser request whose ``Origin`` isn't the Studio shell (or the same origin in
  ``--web`` mode) is rejected with 403. CORS alone is not enough: it only hides the response
  from the page, while the side effect (a render, a download) would still happen.
* **Token**: when configured, every route except ``/health`` needs it (``Authorization: Bearer``
  or ``X-GladTalker-Token``). Checked before the body is read, so unauthenticated uploads are
  never spooled to disk.
* **Body size**: ``Content-Length`` is checked up front and streamed bodies are counted as they
  arrive, so oversized uploads are cut off at the limit.

WebSockets can't send headers from browsers, and query strings end up in server logs, so they
authenticate with short-lived, single-use, job-scoped tickets from ``POST /jobs/{id}/ws-ticket``.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

TICKET_TTL_S = 60.0
DEFAULT_BODY_LIMIT = 2 * 1024 * 1024  # JSON requests (scripts are capped at 200k characters)
OPEN_PATHS = ("/health",)

_REDACT_RE = re.compile(r"(?i)\b(token|ticket)=[^&\s\"']+")


def redact(text: str) -> str:
    return _REDACT_RE.sub(r"\1=[redacted]", text)


def origin_allowed(origin: Optional[str], host: Optional[str], allowed: list[str]) -> bool:
    if not origin:
        return True  # non-browser clients (the desktop shell, curl, tests) send no Origin
    if "*" in allowed or origin in allowed:
        return True
    return bool(host) and urlsplit(origin).netloc == host  # same-origin (--web mode)


def token_matches(supplied: Optional[str], token: str) -> bool:
    return hmac.compare_digest((supplied or "").encode(), token.encode())


def _header(scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class StudioGuard:
    """ASGI middleware enforcing Origin, token, and body-size rules on HTTP requests."""

    def __init__(self, app, *, allowed_origins: list[str], token: Optional[str],
                 body_limit_for: Callable[[str], int]):
        self.app = app
        self.allowed_origins = allowed_origins
        self.token = token
        self.body_limit_for = body_limit_for

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if not origin_allowed(_header(scope, b"origin"), _header(scope, b"host"), self.allowed_origins):
            return await _reject(send, 403, "This origin is not allowed to use GladTalker Studio.")

        if self.token and path not in OPEN_PATHS and scope.get("method") != "OPTIONS":
            supplied = _header(scope, b"x-gladtalker-token")
            auth = _header(scope, b"authorization") or ""
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
            if not token_matches(supplied, self.token):
                return await _reject(send, 401, "Missing or invalid Studio token.")

        limit = self.body_limit_for(path)
        length = _header(scope, b"content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            return await _reject(send, 413, _too_large_message(limit))

        received = 0
        started = False
        rejected = False

        async def limited_receive():
            # When a streamed body crosses the limit, answer 413 ourselves and tell the app the
            # client went away, so it stops reading (and spooling) immediately. Raising instead
            # would be swallowed by FastAPI's body parsing and turned into a 400.
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    rejected = True
                    if not started:
                        await _reject(send, 413, _too_large_message(limit))
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if rejected:
                return  # a 413 was already sent; drop whatever the app tries to answer
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, guarded_send)


def _too_large_message(limit: int) -> str:
    return f"Request is larger than {limit / (1024 * 1024):.0f} MB."


async def _reject(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class TicketStore:
    """Single-use, short-lived WebSocket tickets bound to one job."""

    def __init__(self, ttl_s: float = TICKET_TTL_S, clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self._clock = clock
        self._tickets: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, job_id: str) -> str:
        ticket = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._tickets = {t: v for t, v in self._tickets.items() if v[1] > now}  # drop expired
            self._tickets[ticket] = (job_id, now + self.ttl_s)
        return ticket

    def redeem(self, ticket: Optional[str], job_id: str) -> bool:
        if not ticket:
            return False
        with self._lock:
            entry = self._tickets.pop(ticket, None)
        return bool(entry) and entry[0] == job_id and entry[1] > self._clock()


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


_redaction_lock = threading.Lock()


def install_log_redaction(logger_names=("uvicorn.error", "uvicorn.access", "uvicorn")) -> None:
    """Strip ``token=`` / ``ticket=`` values from server logs (handshake and access lines)."""
    with _redaction_lock:
        for name in logger_names:
            logger = logging.getLogger(name)
            if not any(isinstance(f, _RedactingFilter) for f in logger.filters):
                logger.addFilter(_RedactingFilter())
