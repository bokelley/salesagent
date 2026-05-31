"""Regression tests for embedded buyer identity through the SDK auth gate."""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from adcp.server.auth import (
    A2ABearerAuthMiddleware,
    BearerTokenAuth,
    BearerTokenAuthMiddleware,
    Principal,
    current_principal,
    current_tenant,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.core.embedded_identity_tokens import resolve_embedded_identity_token
from src.core.middleware.embedded_buyer_auth_bridge import EmbeddedBuyerAuthBridgeMiddleware


def _tools_call_body() -> dict:
    return {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "create_media_buy"}, "id": 1}


def _validate_token(token: str) -> Principal | None:
    if token == "real-token":
        return Principal(caller_identity="real-principal", tenant_id="real-tenant")
    embedded = resolve_embedded_identity_token(token)
    if embedded is not None:
        return Principal(caller_identity=embedded.principal_id, tenant_id=embedded.tenant_id)
    return None


def _identity_response() -> JSONResponse:
    return JSONResponse(
        {
            "principal": current_principal.get(),
            "tenant": current_tenant.get(),
        }
    )


def _build_mcp_app() -> EmbeddedBuyerAuthBridgeMiddleware:
    async def handler(request: Request) -> JSONResponse:
        return _identity_response()

    app = Starlette(routes=[Route("/mcp/", handler, methods=["POST"])])
    app.add_middleware(
        BearerTokenAuthMiddleware,
        validate_token=_validate_token,
        legacy_header_aliases=["x-adcp-auth"],
    )
    return EmbeddedBuyerAuthBridgeMiddleware(app)


def _build_a2a_app() -> EmbeddedBuyerAuthBridgeMiddleware:
    async def handler(request: Request) -> JSONResponse:
        return _identity_response()

    app = Starlette(routes=[Route("/", handler, methods=["POST"])])
    auth = BearerTokenAuth(validate_token=_validate_token)
    return EmbeddedBuyerAuthBridgeMiddleware(A2ABearerAuthMiddleware(app, auth))


def _trusted_client(app) -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def _identity_headers() -> dict[str, str]:
    return {
        "X-Principal-Id": "principal_a",
        "X-Identity-Email": "buyer@example.com",
        "X-Identity-Org-Id": "org_a",
        "X-Identity-Role": "admin",
        "X-Identity-Source": "storefront",
        "x-adcp-tenant": "tenant_a",
    }


def _embedded_tenant() -> dict:
    return {"tenant_id": "tenant_a", "is_embedded": True, "external_org_id": "org_a"}


@contextmanager
def _embedded_principal_lookup(principal_id: str | None = "principal_a"):
    session = MagicMock()
    result = MagicMock()
    principal = MagicMock(principal_id=principal_id) if principal_id is not None else None
    result.first.return_value = principal
    session.scalars.return_value = result
    with (
        patch("src.admin.utils.embedded_mode_auth.is_managed_instance", return_value=True),
        patch("src.core.auth.get_db_session") as mock_db,
    ):
        mock_db.return_value.__enter__ = MagicMock(return_value=session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        yield


def test_mcp_mutation_accepts_trusted_embedded_identity_without_bearer_or_cidr_config() -> None:
    """The bridge authenticates before BearerTokenAuthMiddleware can 401."""
    app = _build_mcp_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        _embedded_principal_lookup(),
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "", "BUYER_PROTOCOL_ALLOWED_CIDR": ""}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/mcp/",
            json=_tools_call_body(),
            headers=_identity_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"principal": "principal_a", "tenant": "tenant_a"}


def test_a2a_root_accepts_trusted_embedded_identity_without_bearer() -> None:
    """The same bridge feeds A2A's Authorization-only bearer middleware."""
    app = _build_a2a_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        _embedded_principal_lookup(),
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "127.0.0.1/32"}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/",
            json={"jsonrpc": "2.0", "method": "message/send", "id": 1},
            headers=_identity_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"principal": "principal_a", "tenant": "tenant_a"}


def test_a2a_legacy_x_adcp_auth_does_not_block_embedded_identity() -> None:
    """A2A ignores x-adcp-auth, so it must not suppress the embedded bridge."""
    app = _build_a2a_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        _embedded_principal_lookup(),
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "127.0.0.1/32"}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/",
            json={"jsonrpc": "2.0", "method": "message/send", "id": 1},
            headers={"x-adcp-auth": "real-token", **_identity_headers()},
        )

    assert response.status_code == 200
    assert response.json() == {"principal": "principal_a", "tenant": "tenant_a"}


def test_existing_bearer_credential_wins_over_embedded_headers() -> None:
    """A bad or present bearer must not be silently replaced by X-Principal-Id."""
    app = _build_mcp_app()

    with (
        patch("src.core.middleware.embedded_buyer_auth_bridge._detect_tenant") as mock_detect,
        patch("src.core.middleware.embedded_buyer_auth_bridge._try_resolve_embedded_buyer_identity") as mock_resolve,
        TestClient(app) as client,
    ):
        response = client.post(
            "/mcp/",
            json=_tools_call_body(),
            headers={
                "x-adcp-auth": "real-token",
                "X-Principal-Id": "principal_a",
                "x-adcp-tenant": "tenant_a",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"principal": "real-principal", "tenant": "real-tenant"}
    mock_detect.assert_not_called()
    mock_resolve.assert_not_called()


def test_non_bearer_authorization_does_not_block_embedded_identity() -> None:
    """Only bearer-shaped Authorization is a credential for the SDK gate."""
    app = _build_mcp_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        _embedded_principal_lookup(),
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "127.0.0.1/32"}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/mcp/",
            json=_tools_call_body(),
            headers={
                "Authorization": "Basic not-a-bearer-token",
                **_identity_headers(),
            },
        )

    assert response.status_code == 200
    assert response.json() == {"principal": "principal_a", "tenant": "tenant_a"}


def test_invalid_embedded_principal_returns_403() -> None:
    """Invalid trusted headers should not become a 500 from the bridge."""
    app = _build_mcp_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        _embedded_principal_lookup(principal_id=None),
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "127.0.0.1/32"}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/mcp/",
            json=_tools_call_body(),
            headers={**_identity_headers(), "X-Principal-Id": "unknown"},
        )

    assert response.status_code == 403


def test_embedding_entity_mismatch_returns_specific_403() -> None:
    app = _build_mcp_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        _embedded_principal_lookup(),
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "127.0.0.1/32"}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/mcp/",
            json=_tools_call_body(),
            headers={**_identity_headers(), "X-Identity-Org-Id": "org_other"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "identity_org_mismatch"
    assert "embedding entity id" in response.json()["message"]


def test_embedded_identity_requires_allowed_source_cidr() -> None:
    app = _build_mcp_app()

    with (
        patch(
            "src.core.middleware.embedded_buyer_auth_bridge._detect_tenant",
            return_value=("tenant_a", _embedded_tenant()),
        ),
        patch("src.core.middleware.embedded_buyer_auth_bridge._try_resolve_embedded_buyer_identity") as mock_resolve,
        patch.dict(os.environ, {"BUYER_PROTOCOL_ALLOWED_CIDRS": "10.0.0.0/8"}),
        _trusted_client(app) as client,
    ):
        response = client.post(
            "/mcp/",
            json=_tools_call_body(),
            headers={"X-Principal-Id": "principal_a", "x-adcp-tenant": "tenant_a"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "network_policy_denied"
    mock_resolve.assert_not_called()


@pytest.mark.parametrize("path", ["/admin/", "/health", "/static/app.css"])
def test_non_buyer_protocol_paths_are_not_bridged(path: str) -> None:
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = EmbeddedBuyerAuthBridgeMiddleware(Starlette(routes=[Route("/{path:path}", handler, methods=["POST"])]))

    with (
        patch("src.core.middleware.embedded_buyer_auth_bridge._detect_tenant") as mock_detect,
        TestClient(app) as client,
    ):
        response = client.post(path, json=_tools_call_body(), headers={"X-Principal-Id": "principal_a"})

    assert response.status_code == 200
    mock_detect.assert_not_called()


@pytest.mark.parametrize("path", ["/.well-known/agent-card.json", "/.well-known/agent.json"])
def test_public_discovery_paths_are_not_bridged(path: str) -> None:
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = EmbeddedBuyerAuthBridgeMiddleware(Starlette(routes=[Route("/{path:path}", handler, methods=["GET"])]))

    with (
        patch("src.core.middleware.embedded_buyer_auth_bridge._detect_tenant") as mock_detect,
        TestClient(app) as client,
    ):
        response = client.get(path, headers={"X-Principal-Id": "principal_a"})

    assert response.status_code == 200
    mock_detect.assert_not_called()
