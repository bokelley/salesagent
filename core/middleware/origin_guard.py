"""ASGI middleware for buyer-protocol Origin validation.

FastMCP couples Host and Origin checks behind one
``enable_dns_rebinding_protection`` flag. Routed salesagent deployments let
``SubdomainTenantMiddleware`` own Host validation because the tenant set is
dynamic, but they still need the browser Origin check on buyer-protocol
requests.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.core.signing.middleware import _is_buyer_protocol_path


class BuyerProtocolOriginGuardMiddleware:
    """Validate ``Origin`` on buyer-protocol HTTP requests.

    Mirrors FastMCP's Origin matching contract: absent Origin is allowed,
    exact entries match, and entries ending in ``:*`` match any port for
    the same base origin. Uses the same path predicate as
    ``SigningVerifyMiddleware`` so MCP and A2A stay aligned.
    """

    def __init__(self, app: Any, *, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.allowed_origins = tuple(origin for origin in allowed_origins if origin)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and _is_buyer_protocol_path(scope.get("path", "")):
            origin = self._resolve_header(scope, "origin")
            if not self._is_allowed_origin(origin):
                await self._send_invalid_origin(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _resolve_header(scope: dict, header_name: str) -> str | None:
        expected = header_name.lower()
        for raw_name, raw_value in scope.get("headers", ()):
            if raw_name.decode("latin-1").lower() == expected:
                return raw_value.decode("latin-1")
        return None

    def _is_allowed_origin(self, origin: str | None) -> bool:
        if not origin:
            return True
        if origin in self.allowed_origins:
            return True
        for allowed in self.allowed_origins:
            if allowed.endswith(":*") and origin.startswith(allowed[:-2] + ":"):
                return True
        return False

    @staticmethod
    async def _send_invalid_origin(send: Any) -> None:
        body = b"Invalid Origin header"
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


MCPOriginGuardMiddleware = BuyerProtocolOriginGuardMiddleware
