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
