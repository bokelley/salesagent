"""Cross-transport wire envelope contract test for delegate error translation.

In-process integration test: builds the real ASGI app via
:func:`core.main.build_app` and drives both transports through
:class:`httpx.ASGITransport`. Asserts that a typed/validation error raised
inside ``_delegate_*`` projects onto the spec-mandated AdCP error envelope
on BOTH wire paths:

* **MCP** (``/mcp/``): ``CallToolResult.structuredContent.adcp_error.code``
* **A2A** (host root, JSON-RPC ``message/send``):
  ``result.artifacts[0].parts[0].data.adcp_error.code``

Why this exists alongside ``tests/unit/test_delegate_typed_error_translation.py``:
the unit test asserts on the in-process framework :class:`AdcpError` the
delegate re-raises. It does NOT exercise the A2A executor's catch path
(``_send_adcp_error``) or the MCP dispatcher's projection — a regression
that mangles the wire envelope on either side (or wraps the typed error
as ``INTERNAL_ERROR`` at the framework boundary) passes the unit test
and fails buyers. This test pins the wire surface end-to-end.

The first case (``ValidationError → INVALID_REQUEST``) is the regression
test for the fix that catches pydantic ``ValidationError`` inside the
:func:`core.platforms._delegate.translate_adcp_errors` decorator. Without
that catch, the framework's generic ``except Exception`` wraps it as
``INTERNAL_ERROR: "Platform method 'update_media_buy' raised ValidationError"``
— on A2A this lands as ``"Task failed"`` with no actionable signal.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.harness._asgi_app import run_on_app_loop


def _bad_update_media_buy_payload(principal_id: str) -> dict[str, Any]:
    """Wire patch that triggers ``pydantic.ValidationError`` inside the
    delegate's ``_coerce_to_request_model`` step.

    Threading the needle:

    * The framework validates the body against the **library**
      ``UpdateMediaBuyRequest`` / ``PackageUpdate`` types first. Both
      declare ``extra='allow'`` (forward-compat with future spec fields),
      so an unknown key on a package passes upstream validation.
    * Inside the delegate, ``_coerce_to_request_model`` re-validates the
      patch against our **stricter** :class:`AdCPPackageUpdate` subclass
      (``extra='forbid'`` in dev/test mode). The unknown key now raises
      pydantic ``ValidationError`` — that's the exception the decorator's
      ``except ValidationError`` branch must translate to
      ``INVALID_REQUEST`` with ``recovery='correctable'``.

    Without that translation, the framework's generic ``except Exception``
    wraps it as ``INTERNAL_ERROR: "Platform method 'update_media_buy'
    raised ValidationError"`` — on A2A this lands as "Task failed".
    """
    return {
        "media_buy_id": "mb_validation_target",
        "account": {"account_id": principal_id},
        "idempotency_key": f"wire-test-{uuid.uuid4().hex[:8]}",
        "packages": [
            {
                "package_id": "pkg-1",
                # Unknown key — library PackageUpdate allows extras; our
                # AdCPPackageUpdate forbids them in dev/test mode.
                "salesagent_unknown_field": "trigger ValidationError in delegate",
            }
        ],
    }


@pytest.fixture
def authenticated_principal(integration_db):
    """Create a Tenant + Principal so the bearer middleware accepts requests.

    Returns the access_token the test passes through ``x-adcp-auth`` (MCP)
    and ``Authorization: Bearer`` (A2A). Both transports share the same
    BearerTokenAuth handler — one Principal serves both.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import ALL_FACTORIES, PrincipalFactory, TenantFactory

    engine = get_engine()
    session = SASession(bind=engine)
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session

    suffix = uuid.uuid4().hex[:8]
    tenant_id = f"wire_test_{suffix}"
    principal_id = f"wire_principal_{suffix}"
    access_token = f"wire_token_{suffix}"

    try:
        TenantFactory(tenant_id=tenant_id, subdomain=tenant_id)
        PrincipalFactory(
            tenant_id=tenant_id,
            principal_id=principal_id,
            access_token=access_token,
        )
        session.commit()
        yield {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "access_token": access_token,
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


@pytest.mark.requires_db
def test_validation_error_wire_envelope_mcp(authenticated_principal) -> None:
    """End-to-end MCP wire test: pydantic ``ValidationError`` from delegate
    coercion surfaces as ``adcp_error.code == "INVALID_REQUEST"`` with
    ``recovery == "correctable"`` on the structuredContent envelope.

    Without the ``ValidationError`` branch in
    :func:`core.platforms._delegate.translate_adcp_errors`, the framework
    wraps it as ``INTERNAL_ERROR``. The MCP buyer agent then has no way to
    know which field to repair and treats it as a server failure.
    """
    token = authenticated_principal["access_token"]
    tenant_id = authenticated_principal["tenant_id"]
    bad_args = _bad_update_media_buy_payload(authenticated_principal["principal_id"])

    def _factory(app: Any) -> Any:
        def httpx_factory(**hk: Any) -> httpx.AsyncClient:
            hk.setdefault("timeout", 30.0)
            hk["transport"] = httpx.ASGITransport(app=app)
            hk["base_url"] = "http://testserver"
            return httpx.AsyncClient(**hk)

        transport = StreamableHttpTransport(
            url="http://testserver/mcp/",
            headers={
                "x-adcp-auth": token,
                "x-adcp-tenant": tenant_id,
            },
            httpx_client_factory=httpx_factory,
        )

        async def _call() -> Any:
            async with Client(transport) as client:
                # The framework projects typed AdcpError onto BOTH ``isError=True``
                # + ``structuredContent.adcp_error`` (per transport-errors.mdx
                # §MCP Binding). FastMCP's Client raises ToolError on isError
                # results — but the text payload only carries ``code[field]``
                # via :func:`build_mcp_error_result`'s text fallback, losing
                # ``recovery``. We need ``structuredContent``, so call the
                # lower-level ``call_tool_mcp`` which returns the raw
                # ``CallToolResult`` instead of raising.
                return await client.call_tool_mcp("update_media_buy", bad_args)

        return _call()

    result = run_on_app_loop(_factory)

    # CallToolResult: isError=True + structuredContent.adcp_error per
    # transport-errors.mdx §MCP Binding.
    assert result.isError is True, f"Expected isError=True for delegate ValidationError; got result: {result!r}"
    structured = result.structuredContent or {}
    adcp_error = structured.get("adcp_error")
    assert adcp_error is not None, f"Expected structuredContent.adcp_error envelope, got: {structured!r}"
    assert adcp_error.get("code") == "INVALID_REQUEST", (
        f"Expected code='INVALID_REQUEST' on MCP wire, got {adcp_error.get('code')!r}. "
        f"Without the ValidationError translation, the framework wraps it as "
        f"INTERNAL_ERROR ('Platform method raised ValidationError')."
    )
    assert adcp_error.get("code") != "INTERNAL_ERROR", (
        "ValidationError leaked through as INTERNAL_ERROR — translator regression"
    )
    assert adcp_error.get("recovery") == "correctable", (
        f"Expected recovery='correctable' (buyer-fixable), got {adcp_error.get('recovery')!r}"
    )
    field = adcp_error.get("field") or ""
    assert "packages" in field or "salesagent_unknown_field" in field, (
        f"Expected adcp_error.field to surface the offending field path; got {field!r}"
    )


@pytest.mark.requires_db
def test_validation_error_wire_envelope_a2a(authenticated_principal) -> None:
    """End-to-end A2A wire test: pydantic ``ValidationError`` projects onto
    ``Task.artifacts[0].parts[0].data.adcp_error`` per AdCP transport-errors
    §A2A Binding.

    The A2A executor (``adcp.server.a2a_server.AdcpA2AExecutor.execute``)
    catches the framework :class:`AdcpError` re-raised by the delegate and
    publishes a failed task carrying the structured envelope. A regression
    that breaks delegate translation OR the A2A executor's catch path
    fails this test.
    """
    token = authenticated_principal["access_token"]
    tenant_id = authenticated_principal["tenant_id"]
    bad_params = _bad_update_media_buy_payload(authenticated_principal["principal_id"])

    # A2A JSON-RPC ``message/send`` carrying explicit-skill DataPart per the
    # framework's ``_parse_request`` contract — ``{"skill": ..., "parameters":
    # ...}`` keyed in a data part.
    request_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    context_id = str(uuid.uuid4())
    jsonrpc_body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": message_id,
                "contextId": context_id,
                "role": "user",
                "parts": [
                    {
                        "kind": "data",
                        "data": {"skill": "update_media_buy", "parameters": bad_params},
                    }
                ],
            }
        },
    }

    def _factory(app: Any) -> Any:
        async def _call() -> Any:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as client:
                response = await client.post(
                    "/",
                    json=jsonrpc_body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-adcp-tenant": tenant_id,
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json()

        return _call()

    body = run_on_app_loop(_factory)

    # JSON-RPC envelope: {"jsonrpc": "2.0", "id": ..., "result": {...task...}}
    assert "result" in body, f"A2A wire response missing 'result': {json.dumps(body)[:500]}"
    result = body["result"]

    # Failed task carries the adcp_error envelope in artifacts[0].parts[0].data.
    artifacts = result.get("artifacts") or []
    assert artifacts, f"A2A failed task must publish an artifact carrying adcp_error; got: {json.dumps(result)[:500]}"
    parts = artifacts[0].get("parts") or []
    data_part = next((p for p in parts if p.get("kind") == "data"), None)
    assert data_part is not None, (
        f"A2A artifact must include a DataPart with adcp_error; got: {json.dumps(parts)[:500]}"
    )
    adcp_error = data_part.get("data", {}).get("adcp_error")
    assert adcp_error is not None, f"DataPart.data.adcp_error missing; got: {json.dumps(data_part)[:500]}"

    # Wire contract: INVALID_REQUEST + correctable + field path.
    assert adcp_error.get("code") == "INVALID_REQUEST", (
        f"Expected adcp_error.code='INVALID_REQUEST' for pydantic ValidationError "
        f"on A2A wire path, got {adcp_error.get('code')!r}. Without the decorator's "
        f"ValidationError translation, this would be INTERNAL_ERROR ('Task failed')."
    )
    assert adcp_error.get("recovery") == "correctable", (
        f"Expected adcp_error.recovery='correctable', got {adcp_error.get('recovery')!r}"
    )
    field = adcp_error.get("field") or ""
    assert "packages" in field or "salesagent_unknown_field" in field, (
        f"Expected adcp_error.field to surface the offending field path so buyers "
        f"know which field to repair; got {field!r}"
    )
