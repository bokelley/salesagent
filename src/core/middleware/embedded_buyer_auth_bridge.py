"""Bridge trusted embedded buyer identity headers through SDK bearer auth."""

from __future__ import annotations

import json
import logging
import os
from ipaddress import ip_address, ip_network

from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.auth import _try_resolve_embedded_buyer_identity
from src.core.embedded_identity_tokens import issue_embedded_identity_token
from src.core.exceptions import AdCPAuthenticationError
from src.core.resolved_identity import _detect_tenant, _extract_auth_token

_AUTHORIZATION = b"authorization"
_BUYER_PROTOCOL_PREFIXES = ("/mcp", "/a2a")
_PUBLIC_DISCOVERY_PATHS = frozenset({"/.well-known/agent-card.json", "/.well-known/agent.json"})
logger = logging.getLogger(__name__)
_CIDR_WARNING_EMITTED = False


class EmbeddedBuyerAuthBridgeMiddleware:
    """Inject an internal bearer token for trusted embedded buyer calls.

    The SDK bearer middleware rejects mutating MCP/A2A requests before
    salesagent's ``X-Principal-Id`` resolver runs. For managed embedded
    tenants, the trusted network proxy is the credential source. This bridge
    resolves that identity first, then adds an opaque process-local bearer
    token that the normal SDK auth gate can validate.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _is_public_discovery_request(scope) or not _is_buyer_protocol_request(scope):
            await self._app(scope, receive, send)
            return

        raw_headers = list(scope.get("headers") or ())
        if _has_bearer_credential(scope, raw_headers):
            await self._app(scope, receive, send)
            return

        headers = _headers_to_dict(raw_headers)
        tenant_id, tenant_context = _detect_tenant(headers)
        if not tenant_id or tenant_context is None:
            await self._app(scope, receive, send)
            return
        if tenant_context.get("is_embedded") and not _buyer_protocol_source_allowed(scope):
            await _send_forbidden(
                send,
                error="network_policy_denied",
                message="Buyer protocol source is outside BUYER_PROTOCOL_ALLOWED_CIDRS.",
            )
            return

        try:
            principal_id = _try_resolve_embedded_buyer_identity(
                headers,
                tenant_context,
                require_valid_token=True,
            )
        except AdCPAuthenticationError as exc:
            error, message = _embedded_identity_error_response(exc)
            await _send_forbidden(
                send,
                error=error,
                message=message,
            )
            return
        if principal_id is None:
            await self._app(scope, receive, send)
            return

        bridged_scope = dict(scope)
        token = issue_embedded_identity_token(principal_id=principal_id, tenant_id=tenant_id)
        bridged_scope["headers"] = _replace_authorization_with_bearer(raw_headers, token)
        await self._app(bridged_scope, receive, send)


def _is_buyer_protocol_request(scope: Scope) -> bool:
    path = str(scope.get("path") or "")
    if path == "/":
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _BUYER_PROTOCOL_PREFIXES)


def _is_public_discovery_request(scope: Scope) -> bool:
    return str(scope.get("path") or "") in _PUBLIC_DISCOVERY_PATHS


def _has_bearer_credential(scope: Scope, headers: list[tuple[bytes, bytes]]) -> bool:
    """Return whether the current transport already has its own bearer credential."""
    path = str(scope.get("path") or "")
    headers_dict = _headers_to_dict(headers)
    if path == "/":
        authorization = _get_header_case_insensitive(headers_dict, "authorization")
        return bool(authorization and authorization.lower().startswith("bearer ") and authorization[7:].strip())

    token, _ = _extract_auth_token(headers_dict)
    return token is not None


def _get_header_case_insensitive(headers: dict[str, str], name: str) -> str | None:
    lower_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lower_name:
            return value
    return None


def _embedded_identity_error_response(exc: AdCPAuthenticationError) -> tuple[str, str]:
    details = getattr(exc, "details", None) or {}
    code = details.get("error_code")
    if code == "IDENTITY_ORG_MISMATCH":
        return "identity_org_mismatch", str(exc)
    return "identity_required", "Embedded buyer identity headers are missing, malformed, or unauthorized."


def _headers_to_dict(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    result: dict[str, str] = {}
    seen: set[str] = set()
    for name, value in headers:
        key = name.decode("latin-1")
        lower_key = key.lower()
        if lower_key not in seen:
            result[key] = value.decode("latin-1")
            seen.add(lower_key)
    return result


def _replace_authorization_with_bearer(
    headers: list[tuple[bytes, bytes]],
    token: str,
) -> list[tuple[bytes, bytes]]:
    bridged = [(name, value) for name, value in headers if name.lower() != _AUTHORIZATION]
    bridged.append((_AUTHORIZATION, f"Bearer {token}".encode("ascii")))
    return bridged


def _buyer_protocol_source_allowed(scope: Scope) -> bool:
    global _CIDR_WARNING_EMITTED

    raw_cidrs = os.environ.get("BUYER_PROTOCOL_ALLOWED_CIDRS") or os.environ.get("BUYER_PROTOCOL_ALLOWED_CIDR")
    if not raw_cidrs:
        if not _CIDR_WARNING_EMITTED:
            logger.warning(
                "BUYER_PROTOCOL_ALLOWED_CIDRS is not configured; embedded buyer auth bridge is relying on "
                "MANAGED_INSTANCE plus trusted identity-header injection only."
            )
            _CIDR_WARNING_EMITTED = True
        return True

    client = scope.get("client")
    if not client:
        return False
    client_host = client[0]
    try:
        client_ip = ip_address(client_host)
    except ValueError:
        return False

    for raw_cidr in raw_cidrs.split(","):
        cidr = raw_cidr.strip()
        if not cidr:
            continue
        try:
            if client_ip in ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


async def _send_forbidden(send: Send, *, error: str, message: str) -> None:
    body = json.dumps({"error": error, "message": message}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
