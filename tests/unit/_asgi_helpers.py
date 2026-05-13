"""Shared helpers for ASGI middleware unit tests.

Provides a single ``capture_asgi_response`` driver that wraps a
middleware around a stub inner app, runs one request through it, and
returns the captured ``(status, headers, body, inner_called)`` tuple
for assertion. Used by the agent-card middleware tests to avoid
re-implementing the ASGI message-pump in every file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.types import ASGIApp, Message, Scope


async def capture_asgi_response(
    middleware_factory: Callable[[ASGIApp], Any],
    scope: Scope,
    *,
    inner_status: int = 200,
    inner_body: bytes = b'{"inner":true}',
    inner_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[bytes, bytes], bytes, bool]:
    """Drive a middleware against a stub inner ASGI app.

    Returns ``(status, headers, body, inner_called)`` where
    ``inner_called`` reports whether the inner app was invoked (False
    means the middleware short-circuited the request).
    """
    inner_called = {"yes": False}

    async def inner_app(
        scope: Scope, receive: Callable[[], Awaitable[Message]], send: Callable[[Message], Awaitable[None]]
    ) -> None:
        inner_called["yes"] = True
        await send(
            {
                "type": "http.response.start",
                "status": inner_status,
                "headers": inner_headers
                or [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(inner_body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": inner_body, "more_body": False})

    middleware = middleware_factory(inner_app)

    captured_status = {"code": 0}
    captured_headers: dict[bytes, bytes] = {}
    captured_body = bytearray()

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            captured_status["code"] = message["status"]
            for k, v in message.get("headers", []):
                captured_headers[k.lower()] = v
        elif message["type"] == "http.response.body":
            captured_body.extend(message.get("body") or b"")

    await middleware(scope, receive, send)
    return captured_status["code"], captured_headers, bytes(captured_body), inner_called["yes"]


def http_scope(
    path: str, *, method: str = "GET", headers: list[tuple[str, str]] | None = None, scheme: str = "http"
) -> dict[str, Any]:
    """Build a minimal ASGI HTTP scope for middleware tests."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": scheme,
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in (headers or [])],
    }


# ---------------------------------------------------------------------------
# Scope-mutation middleware testing
# ---------------------------------------------------------------------------
#
# A second pattern that doesn't fit :func:`capture_asgi_response`: middleware
# that inspects or mutates the request scope (headers / state) before
# forwarding to the inner app, without producing a response itself. The
# helpers below let those tests assert on what scope the inner app received.


class CapturingScopeApp:
    """Minimal ASGI inner app that records the scope it was invoked with.

    For testing scope-mutating middleware (dual-credential audit,
    multi-header bearer normalization, etc.) where the assertion is
    "the inner app saw the scope we expected" rather than "the
    middleware emitted the response we expected".
    """

    def __init__(self) -> None:
        self.called = False
        self.scope: dict[str, Any] | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True
        self.scope = scope


def simple_http_scope(headers: list[tuple[bytes, bytes]], *, path: str = "/", method: str = "POST") -> dict[str, Any]:
    """Build a minimal HTTP scope with raw byte headers.

    Distinct from :func:`http_scope` (which accepts str headers and
    encodes) — scope-mutation tests typically already speak the
    bytes-tuple format the ASGI spec uses.
    """
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
    }


async def drive_asgi(scope: dict[str, Any], app: Any) -> None:
    """Run one request through ``app`` with a no-op send/receive pair.

    For middleware that mutates / inspects scope but doesn't depend on
    the response stream. The ``CapturingScopeApp`` inner records what
    arrived; this helper only handles the receive/send plumbing.
    """

    async def _receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def _send(_message: dict[str, Any]) -> None:
        pass

    await app(scope, _receive, _send)
