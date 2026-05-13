"""Normalize ``Authorization: Bearer`` → ``x-adcp-auth`` on the MCP leg.

The MCP leg's :class:`BearerTokenAuthMiddleware` is configured with
``mcp_header_name="x-adcp-auth"`` so it only accepts tokens on the
legacy header baked into early adopters. New clients that follow the
spec — every ``@adcp/sdk`` client out of the box, every buyer agent
that emits ``Authorization: Bearer`` per RFC 6750 — get 401 on every
authenticated MCP call.

This middleware reads ``Authorization: Bearer <token>`` from the
request headers and, when ``x-adcp-auth`` is absent, injects the token
into the scope's headers as ``x-adcp-auth: <token>`` so the inner
SDK middleware validates it. Both-headers-present requests pass
through unchanged — :class:`DualCredentialAuditMiddleware` already
observes that case and logs the divergence; whichever header the SDK
reads wins.

**Position in the ASGI stack.** Must run AFTER :class:`AdminWSGIMount`
(admin Flask paths short-circuit; the bearer scheme is wrong there)
and BEFORE the SDK's :class:`BearerTokenAuthMiddleware` (which reads
``x-adcp-auth`` on the MCP leg). Sits alongside
:class:`DualCredentialAuditMiddleware` — order between the two is
immaterial because this middleware only injects when ``x-adcp-auth``
is absent, and the audit middleware only logs when BOTH credentials
are present with different values; the two predicates are disjoint.

Why not upstream? Filed as a follow-up against
``adcontextprotocol/adcp-client-python`` requesting native multi-header
support on ``BearerTokenAuthMiddleware`` (``header_names`` plural).
Once upstream ships and we bump ``adcp``, this middleware becomes
redundant — drop it from the registration in
``core/main.py:_serve_kwargs`` and delete this module.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

_X_ADCP_AUTH = b"x-adcp-auth"
_AUTHORIZATION = b"authorization"
_BEARER_PREFIX = b"bearer "


class MultiHeaderBearerMiddleware:
    """ASGI middleware that normalizes ``Authorization: Bearer`` → ``x-adcp-auth``
    on the MCP leg when only the RFC 6750 header is present.

    No-op when:

    * ``x-adcp-auth`` is already present (preserve early-adopter behavior).
    * ``Authorization`` is absent or not a Bearer scheme.
    * Scope is not HTTP (lifespan / websocket pass through).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers = scope.get("headers") or ()

        bearer_token: bytes | None = None
        has_adcp_auth = False
        for name, value in headers:
            if name == _X_ADCP_AUTH:
                has_adcp_auth = True
                break
            if name == _AUTHORIZATION and bearer_token is None and value.lower().startswith(_BEARER_PREFIX):
                candidate = value[len(_BEARER_PREFIX) :].strip()
                if candidate:
                    bearer_token = candidate

        if has_adcp_auth or bearer_token is None:
            await self._app(scope, receive, send)
            return

        normalized_headers = list(headers)
        normalized_headers.append((_X_ADCP_AUTH, bearer_token))
        await self._app({**scope, "headers": normalized_headers}, receive, send)
