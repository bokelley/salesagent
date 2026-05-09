"""Tests for ``WellKnownAgentJsonRedirectMiddleware`` (#267).

The 6.x ``@adcp/sdk`` still probes ``/.well-known/agent.json`` for A2A
transport auto-detection. The framework only registers the canonical
``/.well-known/agent-card.json``, so without this middleware the alias
returns no upstream content and Fly's edge collapses the connection
with HTTP 503. The middleware emits a 308 to the canonical path.

End-to-end coverage drives the assembled ASGI app via
:class:`starlette.testclient.TestClient` and verifies that following the
redirect resolves to a 200 with valid agent-card JSON.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from core.middleware.well_known_agent_json_redirect import (
    WellKnownAgentJsonRedirectMiddleware,
)
from tests.unit._asgi_helpers import capture_asgi_response, http_scope


class TestRedirect:
    @pytest.mark.asyncio
    async def test_legacy_path_returns_308_to_canonical(self) -> None:
        status, headers, body, inner_called = await capture_asgi_response(
            WellKnownAgentJsonRedirectMiddleware,
            http_scope("/.well-known/agent.json"),
        )

        assert status == 308
        assert headers[b"location"] == b"/.well-known/agent-card.json"
        assert body == b""
        assert inner_called is False, "inner app must not run on the redirected path"

    @pytest.mark.asyncio
    async def test_canonical_path_passes_through(self) -> None:
        """The canonical path must reach the inner app — only the alias redirects."""
        status, _headers, body, inner_called = await capture_asgi_response(
            WellKnownAgentJsonRedirectMiddleware,
            http_scope("/.well-known/agent-card.json"),
        )

        assert status == 200
        assert body == b'{"inner":true}'
        assert inner_called is True

    @pytest.mark.asyncio
    async def test_unrelated_path_passes_through(self) -> None:
        status, _headers, _body, inner_called = await capture_asgi_response(
            WellKnownAgentJsonRedirectMiddleware,
            http_scope("/some/other/endpoint"),
        )

        assert status == 200
        assert inner_called is True

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self) -> None:
        """WebSocket / lifespan scopes must not be touched."""
        scope = {"type": "websocket", "path": "/.well-known/agent.json", "headers": []}
        _status, _headers, _body, inner_called = await capture_asgi_response(
            WellKnownAgentJsonRedirectMiddleware,
            scope,
        )
        assert inner_called is True


class TestEndToEnd:
    """Drive the middleware over a Starlette ASGI graph with TestClient.

    Verifies the full chain: a request to the alias receives a 308, and
    following the redirect (``follow_redirects=True``) resolves to the
    inner agent-card response.
    """

    @pytest.fixture
    def app(self):
        """Inner app: serves valid agent-card JSON at the canonical path."""

        async def inner(scope, receive, send):
            if scope["type"] != "http":
                return
            if scope["path"] == "/.well-known/agent-card.json":
                payload = json.dumps({"name": "salesagent-core", "url": "http://localhost:8080/"}).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode("latin-1")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": payload, "more_body": False})
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        return WellKnownAgentJsonRedirectMiddleware(inner)

    def test_alias_returns_308_with_location_header(self, app):
        # TestClient does not follow redirects by default — this asserts
        # the redirect itself, not the chained 200.
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/.well-known/agent.json")

        assert resp.status_code == 308
        # Starlette TestClient may emit either an absolute or path-only Location;
        # either form must end with the canonical path.
        assert resp.headers["location"].endswith("/.well-known/agent-card.json")

    def test_following_redirect_resolves_to_canonical_card(self, app):
        client = TestClient(app, follow_redirects=True)
        resp = client.get("/.well-known/agent.json")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["name"] == "salesagent-core"
