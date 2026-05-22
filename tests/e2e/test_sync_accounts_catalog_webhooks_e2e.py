"""E2E coverage for sync_accounts webhook registration and catalog changes."""

from __future__ import annotations

import json
import socket
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import psycopg2
import pytest
import requests
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import parse_tool_result

_EMBEDDED_ORG_ID = "e2e-webhook-org"


class CatalogWebhookReceiver(BaseHTTPRequestHandler):
    received: list[dict[str, Any]] = []

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        CatalogWebhookReceiver.received.append(json.loads(body.decode("utf-8")))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"received"}')

    def log_message(self, format, *args):
        pass


@pytest.fixture
def catalog_webhook_server():
    CatalogWebhookReceiver.received.clear()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = HTTPServer(("0.0.0.0", port), CatalogWebhookReceiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {
        "url": f"http://localhost:{port}/webhook",
        "received": CatalogWebhookReceiver.received,
    }

    server.shutdown()
    server.server_close()
    CatalogWebhookReceiver.received.clear()


def _db_context(live_server: dict[str, Any], test_auth_token: str) -> dict[str, str]:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT tenant_id, principal_id FROM principals WHERE access_token = %s",
                (test_auth_token,),
            )
            row = cursor.fetchone()
            assert row is not None, "ci-test principal must exist"
            cursor.execute(
                "UPDATE tenants SET external_org_id = %s WHERE tenant_id = %s",
                (_EMBEDDED_ORG_ID, row[0]),
            )
        conn.commit()
    return {"tenant_id": row[0], "principal_id": row[1]}


def _insert_deletable_product(live_server: dict[str, Any], tenant_id: str, product_id: str) -> None:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO products (
                    tenant_id,
                    product_id,
                    name,
                    description,
                    format_ids,
                    targeting_template,
                    delivery_type,
                    property_tags,
                    delivery_measurement,
                    reporting_capabilities,
                    property_targeting_allowed,
                    signal_targeting_allowed
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'E2E catalog webhook product',
                    %s::jsonb,
                    '{}'::jsonb,
                    'non_guaranteed',
                    '["all_inventory"]'::jsonb,
                    '{"provider":"publisher"}'::jsonb,
                    '{"available":false}'::jsonb,
                    false,
                    true
                )
                """,
                (
                    tenant_id,
                    product_id,
                    f"E2E Catalog Webhook {product_id}",
                    json.dumps(
                        [
                            {
                                "agent_url": "https://creative.adcontextprotocol.org",
                                "id": "display_300x250",
                            }
                        ]
                    ),
                ),
            )
        conn.commit()


def _wait_for_payload(
    received: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for payload in received:
            if predicate(payload):
                return payload
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for matching webhook. Received: {received!r}")


def _embedded_admin_headers(base_url: str) -> dict[str, str]:
    return {
        "Origin": base_url.rstrip("/"),
        "X-Identity-Email": "e2e-admin@example.com",
        "X-Identity-Org-Id": _EMBEDDED_ORG_ID,
        "X-Identity-Role": "admin",
        "X-Identity-Source": "e2e",
        "X-Identity-User-Id": "e2e-admin",
    }


async def _register_webhook_via_sync_accounts(
    live_server: dict[str, Any],
    test_auth_token: str,
    webhook_url: str,
) -> tuple[str, str, str]:
    brand_domain = f"e2e-webhook-{uuid.uuid4().hex[:8]}.example"
    operation_id = f"catalog-op-{uuid.uuid4()}"
    validation_token = "client-validation-token"
    headers = {"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"}
    transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)
    async with Client(transport=transport) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert "sync_accounts" in tools
        assert "get_signals" in tools

        result = await client.call_tool(
            "sync_accounts",
            {
                "idempotency_key": f"e2e-sync-{uuid.uuid4()}",
                "accounts": [
                    {
                        "brand": {"domain": brand_domain},
                        "operator": "e2e-operator.example",
                        "billing": "operator",
                    }
                ],
                "push_notification_config": {
                    "url": webhook_url,
                    "operation_id": operation_id,
                    "token": validation_token,
                },
            },
        )
        data = parse_tool_result(result)

    assert data["accounts"][0]["action"] == "created"
    account_id = data["accounts"][0]["account_id"]
    assert account_id
    return account_id, operation_id, validation_token


@pytest.mark.asyncio
async def test_sync_accounts_registration_fires_account_product_and_signal_webhooks(
    docker_services_e2e,
    live_server,
    test_auth_token,
    catalog_webhook_server,
):
    ctx = _db_context(live_server, test_auth_token)
    account_id, operation_id, validation_token = await _register_webhook_via_sync_accounts(
        live_server,
        test_auth_token,
        catalog_webhook_server["url"],
    )

    session = requests.Session()
    admin_headers = _embedded_admin_headers(live_server["admin"])

    account_response = session.post(
        f"{live_server['admin']}/tenant/{ctx['tenant_id']}/accounts/{account_id}/status",
        json={"status": "suspended"},
        headers=admin_headers,
        timeout=20,
    )
    assert account_response.status_code == 200, account_response.text
    account_payload = _wait_for_payload(
        catalog_webhook_server["received"],
        lambda payload: payload.get("type") == "account.status_changed" and payload.get("object_id") == account_id,
    )
    assert account_payload["data"] == {"from_status": "active", "to_status": "suspended"}
    assert account_payload["operation_id"] == operation_id
    assert account_payload["token"] == validation_token
    assert "validation_token" not in account_payload

    product_id = f"e2e_product_{uuid.uuid4().hex[:8]}"
    _insert_deletable_product(live_server, ctx["tenant_id"], product_id)
    product_response = session.delete(
        f"{live_server['admin']}/tenant/{ctx['tenant_id']}/products/{product_id}/delete",
        headers=admin_headers,
        timeout=20,
    )
    assert product_response.status_code == 200, product_response.text
    product_payload = _wait_for_payload(
        catalog_webhook_server["received"],
        lambda payload: payload.get("object_type") == "product" and payload.get("object_id") == product_id,
    )
    assert product_payload["type"] == "catalog.changed"
    assert product_payload["action"] == "deleted"
    assert product_payload["refresh_tool"] == "get_products"
    assert product_payload["operation_id"] == operation_id
    assert product_payload["token"] == validation_token

    signal_name = f"E2E Segment {uuid.uuid4().hex[:8]}"
    signal_response = session.post(
        f"{live_server['admin']}/tenant/{ctx['tenant_id']}/signals/bulk-create",
        json={
            "items": [
                {
                    "kind": "audience_segment",
                    "segment_id": f"e2e-segment-{uuid.uuid4().hex[:8]}",
                    "segment_name": signal_name,
                }
            ]
        },
        headers=admin_headers,
        timeout=20,
    )
    assert signal_response.status_code == 200, signal_response.text
    signal_id = signal_response.json()["signal_ids"][0]
    signal_payload = _wait_for_payload(
        catalog_webhook_server["received"],
        lambda payload: payload.get("object_type") == "signal" and payload.get("object_id") == signal_id,
    )
    assert signal_payload["type"] == "catalog.changed"
    assert signal_payload["action"] == "created"
    assert signal_payload["refresh_tool"] == "get_signals"
    assert signal_payload["operation_id"] == operation_id
    assert signal_payload["token"] == validation_token

    headers = {"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"}
    transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)
    requested_signal_id = {
        "source": "agent",
        "agent_url": "https://salesagent.adcontextprotocol.org/signals",
        "id": signal_id,
    }
    async with Client(transport=transport) as client:
        result = await client.call_tool("get_signals", {"signal_ids": [requested_signal_id]})
        data = parse_tool_result(result)

    assert [signal["signal_id"] for signal in data["signals"]] == [requested_signal_id]
