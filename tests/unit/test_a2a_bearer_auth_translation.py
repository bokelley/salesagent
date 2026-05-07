"""End-to-end ASGI test for A2A bearer-token translation.

Regression for #104: a valid bearer token accepted on ``/mcp`` was
rejected at the A2A surface (host root). The unit tests for
:class:`BearerToAdcpAuthMiddleware` only verify in-isolation header
translation; they don't catch wiring bugs where a future refactor
moves the translation middleware out of the A2A request path or the
SDK auth chain stops reading ``x-adcp-auth``.

This test composes the same ASGI layering production uses around the
A2A leg — :class:`BearerToAdcpAuthMiddleware` outside, the SDK's
:class:`A2ABearerAuthMiddleware` inside — and drives it with an HTTP
scope carrying ``Authorization: Bearer`` (RFC 6750 / what a2a-sdk's
official client emits). On the success path the inner app records the
authenticated principal; on rejection the middleware emits HTTP 401
with body ``{"error": "invalid_token", ...}``.

Why not exercise :func:`core.main.build_app`: full app build pulls a
DB session for tenant + principal lookup; a unit test should fail
fast and stay deterministic. The composition under test mirrors
``_build_mcp_and_a2a_app``'s wiring exactly (production wraps
``A2ABearerAuthMiddleware`` around the inner A2A Starlette via
``_wrap_a2a_with_auth`` and applies the operator middleware list
outermost via ``_apply_asgi_middleware``).
"""

from __future__ import annotations

from typing import Any

import pytest
from adcp.server.auth import A2ABearerAuthMiddleware, BearerTokenAuth, Principal

from core.middleware.bearer_to_adcp_auth import BearerToAdcpAuthMiddleware


class _CapturingApp:
    """ASGI sink that records the scope the A2A middleware passed through."""

    def __init__(self) -> None:
        self.captured_scope: dict[str, Any] | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.captured_scope = scope
        # 200 with empty body so the auth middleware sees a clean exit.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _post_scope(headers: list[tuple[bytes, bytes]], path: str = "/") -> dict[str, Any]:
    """Build an ASGI HTTP POST scope mimicking an A2A JSON-RPC request."""
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
    }


async def _drain(messages: list[dict[str, Any]], scope: dict[str, Any], app: Any) -> None:
    """Run ``app(scope)`` and collect every outbound message into ``messages``."""

    async def _receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def _send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, _receive, _send)


def _build_a2a_chain(
    inner_app: Any,
    *,
    valid_tokens: dict[str, Principal],
) -> Any:
    """Compose the production A2A wiring around ``inner_app``.

    Layering — outermost first:

    1. :class:`BearerToAdcpAuthMiddleware` translates RFC 6750
       ``Authorization: Bearer`` to the canonical ``x-adcp-auth``.
    2. :class:`A2ABearerAuthMiddleware` reads ``x-adcp-auth`` and
       gates the request on a valid principal.
    3. ``inner_app`` — the captured A2A request handler.
    """
    auth = BearerTokenAuth(
        validate_token=valid_tokens.get,
        header_name="x-adcp-auth",
        bearer_prefix_required=False,
    )
    a2a_with_auth = A2ABearerAuthMiddleware(inner_app, auth)
    return BearerToAdcpAuthMiddleware(a2a_with_auth)


@pytest.mark.asyncio
async def test_a2a_accepts_authorization_bearer_after_translation():
    """RFC 6750 ``Authorization: Bearer <token>`` on the A2A surface
    authenticates via the same bearer token MCP accepts.

    Regression for #104: without the translation step the SDK's A2A
    auth middleware sees no ``x-adcp-auth`` header and emits HTTP 401
    with body ``{"error": "invalid_token", ...}`` — exactly the
    failure observed against deployed Wonderstruck staging.
    """
    principal = Principal(caller_identity="buyer-1", tenant_id="acme")
    inner = _CapturingApp()
    app = _build_a2a_chain(inner, valid_tokens={"valid-token": principal})

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([(b"authorization", b"Bearer valid-token")]), app)

    # Inner app reached → auth passed end-to-end.
    assert inner.captured_scope is not None, (
        "A2A middleware rejected the request before the inner app was reached. "
        "Bearer-translation likely didn't run on the A2A path."
    )
    # SDK middleware writes the principal to scope['auth'] on success.
    assert inner.captured_scope.get("auth") is principal
    # Status 200 (not 401) — confirms the SDK middleware accepted the token.
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 200, (
        f"Expected 200 OK after successful auth; got {starts[0]['status'] if starts else 'no response'}"
    )


@pytest.mark.asyncio
async def test_a2a_accepts_x_adcp_auth_header_directly():
    """The canonical ``x-adcp-auth`` header still authenticates on A2A
    — the translation middleware is a no-op when the canonical header
    is already present, so MCP-style buyers keep working."""
    principal = Principal(caller_identity="buyer-1", tenant_id="acme")
    inner = _CapturingApp()
    app = _build_a2a_chain(inner, valid_tokens={"valid-token": principal})

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([(b"x-adcp-auth", b"valid-token")]), app)

    assert inner.captured_scope is not None
    assert inner.captured_scope.get("auth") is principal


@pytest.mark.asyncio
async def test_a2a_rejects_missing_authorization():
    """No bearer header on A2A → SDK middleware returns 401
    ``invalid_token`` (the symptom in #104)."""
    inner = _CapturingApp()
    app = _build_a2a_chain(inner, valid_tokens={"valid-token": Principal(caller_identity="x")})

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([]), app)

    assert inner.captured_scope is None, "Inner app must not be reached without auth."
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 401


@pytest.mark.asyncio
async def test_a2a_rejects_authorization_bearer_with_unknown_token():
    """``Authorization: Bearer <unknown>`` translates but the inner
    SDK middleware rejects on the validator returning ``None``."""
    inner = _CapturingApp()
    app = _build_a2a_chain(inner, valid_tokens={"valid-token": Principal(caller_identity="x")})

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([(b"authorization", b"Bearer wrong-token")]), app)

    assert inner.captured_scope is None
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 401


@pytest.mark.asyncio
async def test_a2a_chain_matches_production_middleware_order():
    """Drive the request through the *exact* operator middleware list
    that ``_serve_kwargs`` builds (``AdminWSGIMount`` first,
    ``BearerToAdcpAuthMiddleware`` next, etc.) wrapped around the SDK
    A2A leg.

    Recreating the full ``_apply_asgi_middleware`` order catches a
    class of regressions a "compose two middlewares directly" test
    cannot: e.g. an intermediate middleware that copies the scope
    without preserving the injected ``x-adcp-auth`` header, or a path
    filter that bypasses bearer translation on the A2A surface.
    """
    from unittest.mock import MagicMock, patch

    from adcp.server.serve import _apply_asgi_middleware

    from core import main as core_main

    principal = Principal(caller_identity="buyer-1", tenant_id="acme")

    # Build the inner app: A2A wraps a sink with the SDK's auth
    # middleware, exactly as ``_wrap_a2a_with_auth`` does in production.
    inner = _CapturingApp()
    auth_config = BearerTokenAuth(
        validate_token=lambda t: principal if t == "valid-token" else None,
        header_name="x-adcp-auth",
        bearer_prefix_required=False,
    )
    a2a_with_auth = A2ABearerAuthMiddleware(inner, auth_config)

    # Build the operator middleware list via ``_serve_kwargs`` —
    # mock out the DB-backed router/admin construction so the test is
    # pure-Python.
    with (
        patch.object(core_main, "build_router", return_value=MagicMock()),
        patch("src.admin.app.create_app", return_value=MagicMock()),
        patch("core.main.build_subdomain_router", return_value=MagicMock()),
    ):
        kwargs = core_main._serve_kwargs(include_scheduler=False, include_subdomain_routing=False)

    # Strip middlewares that need real router/admin/scheduler infra to
    # function — for this test we only care about whether
    # BearerToAdcpAuthMiddleware delivers ``x-adcp-auth`` to the SDK
    # auth chain. Keep AdminWSGIMount (host-aware passthrough),
    # BearerToAdcpAuthMiddleware, and SpecDefaultsMiddleware.
    keep_classes = {
        BearerToAdcpAuthMiddleware,
        # AdminWSGIMount needs wsgi_app kwarg already set — keep it.
    }
    operator_middleware = [
        entry
        for entry in kwargs["asgi_middleware"]
        if entry[0] in keep_classes or entry[0].__name__ == "AdminWSGIMount"
    ]
    # Sanity check that the production list still contains the
    # translation middleware — the locked ordering test (in
    # ``test_serve_kwargs_middleware_order``) covers that fully, but a
    # bare assertion here keeps this test self-contained on regressions.
    assert any(e[0] is BearerToAdcpAuthMiddleware for e in operator_middleware), (
        "BearerToAdcpAuthMiddleware missing from production middleware list"
    )

    app = _apply_asgi_middleware(a2a_with_auth, operator_middleware)

    messages: list[dict[str, Any]] = []
    # Include a Host header — AdminWSGIMount inspects it to decide
    # whether to dispatch admin paths to Flask. A non-admin host (e.g.
    # the tenant subdomain) falls through to the A2A leg.
    scope = _post_scope(
        [
            (b"host", b"wonderstruck.sales-agent.scope3.com"),
            (b"authorization", b"Bearer valid-token"),
        ]
    )
    await _drain(messages, scope, app)

    assert inner.captured_scope is not None, (
        "Production middleware ordering dropped the A2A request before "
        "the inner app — Bearer translation likely did not run on "
        "the A2A path."
    )
    assert inner.captured_scope.get("auth") is principal
