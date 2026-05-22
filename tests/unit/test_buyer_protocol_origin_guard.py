"""Unit tests for BuyerProtocolOriginGuardMiddleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.middleware.origin_guard import BuyerProtocolOriginGuardMiddleware


def _http_scope(*, path: str = "/mcp/", origin: str | None = None, header_name: bytes = b"origin") -> dict:
    headers: list[tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((header_name, origin.encode("latin-1")))
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
    }


async def _terminal_app(scope: dict, receive: Any, send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 204,
            "headers": [(b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


@pytest.mark.asyncio
class TestBuyerProtocolOriginGuardMiddleware:
    async def test_absent_origin_is_allowed(self):
        app = BuyerProtocolOriginGuardMiddleware(_terminal_app, allowed_origins=["https://admin.example.com"])
        send = AsyncMock()

        await app(_http_scope(), AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 204

    async def test_exact_origin_is_allowed(self):
        app = BuyerProtocolOriginGuardMiddleware(_terminal_app, allowed_origins=["https://admin.example.com"])
        send = AsyncMock()

        await app(_http_scope(origin="https://admin.example.com"), AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 204

    async def test_origin_header_lookup_is_case_insensitive(self):
        app = BuyerProtocolOriginGuardMiddleware(_terminal_app, allowed_origins=["https://admin.example.com"])
        send = AsyncMock()

        await app(_http_scope(origin="https://admin.example.com", header_name=b"Origin"), AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 204

    async def test_wildcard_port_origin_is_allowed(self):
        app = BuyerProtocolOriginGuardMiddleware(_terminal_app, allowed_origins=["http://localhost:*"])
        send = AsyncMock()

        await app(_http_scope(origin="http://localhost:8000"), AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 204

    async def test_invalid_origin_is_rejected(self):
        inner = AsyncMock()
        app = BuyerProtocolOriginGuardMiddleware(inner, allowed_origins=["https://admin.example.com"])
        send = AsyncMock()

        await app(_http_scope(origin="https://attacker.example.com"), AsyncMock(), send)

        start = send.call_args_list[0].args[0]
        body = send.call_args_list[1].args[0]
        assert start["status"] == 403
        assert body["body"] == b"Invalid Origin header"
        inner.assert_not_called()

    @pytest.mark.parametrize("path", ["/", "/a2a", "/a2a/messages"])
    async def test_a2a_paths_are_checked(self, path):
        inner = AsyncMock()
        app = BuyerProtocolOriginGuardMiddleware(inner, allowed_origins=["https://admin.example.com"])
        send = AsyncMock()

        await app(_http_scope(path=path, origin="https://attacker.example.com"), AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 403
        inner.assert_not_called()

    @pytest.mark.parametrize("path", ["/admin", "/.well-known/agent-card.json"])
    async def test_non_buyer_protocol_paths_are_not_checked(self, path):
        app = BuyerProtocolOriginGuardMiddleware(_terminal_app, allowed_origins=["https://admin.example.com"])
        send = AsyncMock()

        await app(_http_scope(path=path, origin="https://attacker.example.com"), AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 204
