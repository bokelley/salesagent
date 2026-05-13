"""Unit tests for ``MultiHeaderBearerMiddleware``.

The middleware normalizes RFC 6750 ``Authorization: Bearer`` → ``x-adcp-auth``
on the MCP leg so that spec-conformant buyer agents (and the compliance
probe) authenticate against an MCP middleware that only reads
``x-adcp-auth`` (per the early-adopter compatibility config).
"""

from __future__ import annotations

from typing import Any

import pytest

from core.middleware.multi_header_bearer import MultiHeaderBearerMiddleware
from tests.unit._asgi_helpers import CapturingScopeApp, drive_asgi, simple_http_scope


def _scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return simple_http_scope(headers, path="/mcp")


def _header_value(scope: dict[str, Any], name: bytes) -> bytes | None:
    for hname, hvalue in scope.get("headers") or ():
        if hname == name:
            return hvalue
    return None


@pytest.mark.asyncio
async def test_injects_x_adcp_auth_when_only_authorization_present():
    """The canonical fix: ``Authorization: Bearer <token>`` alone arrives
    at the inner SDK middleware as ``x-adcp-auth: <token>`` so the MCP
    leg's bearer validator picks it up. Closes the
    ``security_baseline/probe_api_key`` compliance gap."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    token = b"rfc6750-canonical-token"
    await drive_asgi(_scope([(b"authorization", b"Bearer " + token)]), middleware)

    assert inner.called
    assert inner.scope is not None
    assert _header_value(inner.scope, b"x-adcp-auth") == token, (
        "Authorization: Bearer must be normalized into x-adcp-auth so the "
        "MCP-leg validator (which only reads x-adcp-auth) accepts it"
    )
    # Original Authorization header is preserved — the DualCredentialAudit
    # middleware (and any downstream observer) still sees both.
    assert _header_value(inner.scope, b"authorization") == b"Bearer " + token


@pytest.mark.asyncio
async def test_passthrough_when_x_adcp_auth_already_present():
    """Early-adopter case: client sends ``x-adcp-auth`` directly. No
    mutation — the inner middleware reads its configured header
    unchanged. Both-headers requests also fall through this branch so
    the dual-credential audit middleware can observe divergence."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    headers = [
        (b"x-adcp-auth", b"legacy-token-aaa"),
        (b"authorization", b"Bearer different-token-bbb"),
    ]
    await drive_asgi(_scope(list(headers)), middleware)

    assert inner.called
    assert inner.scope is not None
    # Headers pass through unchanged — no duplicate x-adcp-auth entry.
    forwarded = inner.scope.get("headers") or []
    x_adcp_entries = [v for n, v in forwarded if n == b"x-adcp-auth"]
    assert x_adcp_entries == [b"legacy-token-aaa"], (
        "When x-adcp-auth is already present, middleware must not append "
        "a second entry that could shadow the buyer's intended value"
    )


@pytest.mark.asyncio
async def test_passthrough_when_no_credential_present():
    """Unauthenticated requests pass through untouched — the inner SDK
    bearer middleware emits the canonical 401, and
    :class:`WWWAuthenticateMiddleware` injects the RFC 6750
    challenge header."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    await drive_asgi(_scope([]), middleware)

    assert inner.called
    assert inner.scope is not None
    assert _header_value(inner.scope, b"x-adcp-auth") is None


@pytest.mark.asyncio
async def test_non_bearer_authorization_ignored():
    """``Authorization: Basic ...`` is not a bearer credential. No
    injection — the request reaches the SDK middleware with neither
    bearer header and 401s normally."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    await drive_asgi(_scope([(b"authorization", b"Basic dXNlcjpwYXNz")]), middleware)

    assert inner.called
    assert inner.scope is not None
    assert _header_value(inner.scope, b"x-adcp-auth") is None


@pytest.mark.asyncio
async def test_bearer_prefix_case_insensitive():
    """RFC 6750 §2.1 specifies ``Bearer`` is case-insensitive. Buyers
    that emit ``bearer``, ``BEARER``, etc. must be normalized."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    token = b"mixed-case-token"
    await drive_asgi(_scope([(b"authorization", b"bearer " + token)]), middleware)

    assert inner.called
    assert inner.scope is not None
    assert _header_value(inner.scope, b"x-adcp-auth") == token


@pytest.mark.asyncio
async def test_empty_bearer_token_not_injected():
    """``Authorization: Bearer `` (empty token after the scheme) is not
    a valid credential. Don't inject an empty x-adcp-auth — the SDK
    bearer middleware should treat it as unauthenticated and 401."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    await drive_asgi(_scope([(b"authorization", b"Bearer   ")]), middleware)

    assert inner.called
    assert inner.scope is not None
    assert _header_value(inner.scope, b"x-adcp-auth") is None


@pytest.mark.asyncio
async def test_lifespan_passes_through_without_inspection():
    """Non-HTTP scopes (lifespan, websocket) must pass through without
    header inspection — auth doesn't apply, and the headers field is
    typically absent on lifespan."""
    inner = CapturingScopeApp()
    middleware = MultiHeaderBearerMiddleware(inner)

    lifespan_scope: dict[str, Any] = {"type": "lifespan"}
    await drive_asgi(lifespan_scope, middleware)

    assert inner.called
    # Lifespan scope is passed through unchanged — never mutated.
    assert inner.scope is lifespan_scope
