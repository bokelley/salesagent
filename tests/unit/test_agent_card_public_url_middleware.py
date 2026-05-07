"""Unit tests for ``AgentCardPublicUrlMiddleware``.

Covers #103: the framework's _build_agent_card hardcodes
``http://localhost:{port}/`` and exposes no hook for public-host injection.
This middleware rewrites the URL fields in the
``/.well-known/agent-card.json`` response based on the request's
``X-Forwarded-Host`` / ``Host`` headers so SDK clients reading the card see
the public URL instead of the container's internal socket.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.middleware.agent_card_public_url import AgentCardPublicUrlMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope(path: str, headers: list[tuple[str, str]] | None = None, scheme: str = "http") -> dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "scheme": scheme,
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in (headers or [])],
    }


async def _drive(
    middleware: AgentCardPublicUrlMiddleware,
    scope: dict[str, Any],
    inner_body: bytes,
    inner_status: int = 200,
    inner_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[bytes, bytes], bytes]:
    """Drive the middleware against a fake inner ASGI app and return what it sent.

    The inner app is replaced with a stub that emits ``inner_body`` with the
    given status. The captured ``send`` calls are reassembled into
    ``(status, header_dict, body)`` for assertion.
    """
    captured_status = {"code": 0}
    captured_headers: dict[bytes, bytes] = {}
    captured_body = bytearray()

    async def mock_inner_app(scope, receive, send):
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

    middleware.app = mock_inner_app

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured_status["code"] = message["status"]
            for k, v in message.get("headers", []):
                captured_headers[k.lower()] = v
        elif message["type"] == "http.response.body":
            captured_body.extend(message.get("body") or b"")

    await middleware(scope, receive, send)
    return captured_status["code"], captured_headers, bytes(captured_body)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


CARD_LOOPBACK = {
    "name": "salesagent-core",
    "url": "http://localhost:8080/",
    "supportedInterfaces": [
        {"url": "http://localhost:8080/", "protocolBinding": "JSONRPC", "protocolVersion": "0.3"},
        {"url": "http://localhost:8080/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
    ],
    "preferredTransport": "JSONRPC",
}


class TestAgentCardRewrite:
    @pytest.mark.asyncio
    async def test_rewrites_top_level_url_from_x_forwarded(self) -> None:
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[
                ("host", "internal:8080"),
                ("x-forwarded-host", "wonderstruck.sales-agent.scope3.com"),
                ("x-forwarded-proto", "https"),
            ],
        )

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)
        payload = json.loads(out)

        assert payload["url"] == "https://wonderstruck.sales-agent.scope3.com/"
        for iface in payload["supportedInterfaces"]:
            assert iface["url"] == "https://wonderstruck.sales-agent.scope3.com/"

    @pytest.mark.asyncio
    async def test_falls_back_to_host_header_when_no_xff(self) -> None:
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[("host", "wonderstruck.sales-agent.scope3.com")],
            scheme="https",
        )

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)
        payload = json.loads(out)

        assert payload["url"] == "https://wonderstruck.sales-agent.scope3.com/"

    @pytest.mark.asyncio
    async def test_defaults_to_https_when_no_proto_header(self) -> None:
        """Production deploys all sit behind TLS — emitting http on a TLS
        endpoint would break SDK clients that follow https links."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[("host", "agent.example.com")],
            scheme="",
        )

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)
        payload = json.loads(out)

        assert payload["url"].startswith("https://")

    @pytest.mark.asyncio
    async def test_passes_through_when_no_host_headers(self) -> None:
        """If neither X-Forwarded-Host nor Host is present, leak the localhost
        URL rather than render an empty/garbage one."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope("/.well-known/agent-card.json", headers=[])

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)

        # Body is forwarded unchanged.
        assert out == body

    @pytest.mark.asyncio
    async def test_does_not_rewrite_non_loopback_urls(self) -> None:
        """If the framework already returned a real public URL (e.g. via a
        future config hook), pass it through untouched. We only ever swap
        loopback hosts."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        non_loopback_card = {
            "url": "https://already-public.example.com/",
            "supportedInterfaces": [
                {"url": "https://already-public.example.com/", "protocolVersion": "1.0"},
            ],
        }
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[("x-forwarded-host", "different.example.com"), ("x-forwarded-proto", "https")],
        )

        body = json.dumps(non_loopback_card).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)
        payload = json.loads(out)

        assert payload["url"] == "https://already-public.example.com/"
        assert payload["supportedInterfaces"][0]["url"] == "https://already-public.example.com/"

    @pytest.mark.asyncio
    async def test_unrelated_paths_pass_through_unchanged(self) -> None:
        """No buffering overhead for any path other than the agent card."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/some/other/endpoint",
            headers=[("x-forwarded-host", "wonderstruck.sales-agent.scope3.com")],
        )
        body = b'{"hello": "world"}'

        _status, _headers, out = await _drive(middleware, scope, body)
        assert out == body

    @pytest.mark.asyncio
    async def test_handles_legacy_agent_json_alias(self) -> None:
        """The 0.3 alias /.well-known/agent.json must rewrite the same way."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent.json",
            headers=[("x-forwarded-host", "agent.example.com"), ("x-forwarded-proto", "https")],
        )

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)
        payload = json.loads(out)
        assert payload["url"] == "https://agent.example.com/"

    @pytest.mark.asyncio
    async def test_updates_content_length_header(self) -> None:
        """If the rewritten body has a different size, Content-Length must
        match — otherwise downstream proxies / clients truncate or hang."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[("x-forwarded-host", "very-long-public-hostname.sales-agent.scope3.com")],
            scheme="https",
        )

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, headers, out = await _drive(middleware, scope, body)
        assert headers[b"content-length"] == str(len(out)).encode("latin-1")

    @pytest.mark.asyncio
    async def test_non_json_body_passes_through_unchanged(self) -> None:
        """If the framework ever serves a non-JSON response on the agent-card
        path (e.g. an HTML error page), pass it through rather than corrupt
        it."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[("x-forwarded-host", "agent.example.com")],
        )

        body = b"<html>oops</html>"
        _status, _headers, out = await _drive(middleware, scope, body)
        assert out == body

    @pytest.mark.asyncio
    async def test_strips_extra_xff_entries(self) -> None:
        """X-Forwarded-Host can be a comma-separated list (proxy chain) — use
        the first value, which is the originating client's request."""
        middleware = AgentCardPublicUrlMiddleware(app=None)
        scope = _scope(
            "/.well-known/agent-card.json",
            headers=[
                ("x-forwarded-host", "public.example.com, internal.example.com"),
                ("x-forwarded-proto", "https"),
            ],
        )

        body = json.dumps(CARD_LOOPBACK).encode("utf-8")
        _status, _headers, out = await _drive(middleware, scope, body)
        payload = json.loads(out)
        assert payload["url"] == "https://public.example.com/"
