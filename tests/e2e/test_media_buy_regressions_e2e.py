"""Live E2E regressions for media-buy lifecycle fixes."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    build_update_media_buy_request,
    get_test_date_range,
    parse_tool_result,
)


def _db_one(live_server: dict[str, Any], query: str, params: tuple[Any, ...]) -> tuple[Any, ...]:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            assert row is not None
            return row


def _disable_mock_manual_approval(live_server: dict[str, Any]) -> None:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM tenants WHERE subdomain = 'ci-test'")
            tenant_id = cur.fetchone()[0]
            cur.execute(
                """
                UPDATE tenants
                SET human_review_required = false
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
            cur.execute(
                """
                INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                VALUES (%s, 'mock', false)
                ON CONFLICT (tenant_id)
                DO UPDATE SET adapter_type = 'mock', mock_manual_approval_required = false
                """,
                (tenant_id,),
            )


async def _first_product(client: Client) -> tuple[str, str]:
    products_result = await client.call_tool(
        "get_products",
        {"brand": {"domain": "testbrand.com"}, "brief": "display advertising"},
    )
    products_data = parse_tool_result(products_result)
    product = products_data["products"][0]
    return product["product_id"], product["pricing_options"][0]["pricing_option_id"]


@pytest.mark.asyncio
async def test_pause_resume_persists_and_reads_back_consistently(docker_services_e2e, live_server, test_auth_token):
    _disable_mock_manual_approval(live_server)
    transport = StreamableHttpTransport(
        url=f"{live_server['mcp']}/mcp/",
        headers={"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"},
    )

    async with Client(transport=transport) as client:
        product_id, pricing_option_id = await _first_product(client)
        start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
        create_req = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=5000.0,
            start_time=start_time,
            end_time=end_time,
            brand={"domain": "testbrand.com"},
            pricing_option_id=pricing_option_id,
        )
        create_data = parse_tool_result(await client.call_tool("create_media_buy", create_req))
        media_buy_id = create_data.get("media_buy_id")
        assert media_buy_id, create_data
        assert (
            create_data.get("media_buy_status") == "pending_creatives"
            or create_data.get("status") == "pending_creatives"
        )
        revision = create_data.get("revision") or 1

        pause_req = build_update_media_buy_request(
            media_buy_id=media_buy_id,
            brand={"domain": "testbrand.com"},
            context={"e2e": "pause"},
        )
        pause_req["paused"] = True
        pause_req["revision"] = revision
        pause_data = parse_tool_result(await client.call_tool("update_media_buy", pause_req))
        assert pause_data.get("media_buy_status") == "paused" or pause_data.get("status") == "paused"
        pause_revision = pause_data["revision"]

        db_status, db_is_paused = _db_one(
            live_server,
            "SELECT status, is_paused FROM media_buys WHERE media_buy_id = %s",
            (media_buy_id,),
        )
        assert db_status == "paused"
        assert db_is_paused is True

        list_data = parse_tool_result(await client.call_tool("get_media_buys", {"media_buy_ids": [media_buy_id]}))
        readback = list_data["media_buys"][0]
        assert readback["status"] == "paused"
        assert readback["revision"] == pause_revision

        resume_req = build_update_media_buy_request(
            media_buy_id=media_buy_id,
            brand={"domain": "testbrand.com"},
            context={"e2e": "resume"},
        )
        resume_req["paused"] = False
        resume_req["revision"] = pause_revision
        resume_data = parse_tool_result(await client.call_tool("update_media_buy", resume_req))
        assert (
            resume_data.get("media_buy_status") == "pending_creatives"
            or resume_data.get("status") == "pending_creatives"
        )

        db_status, db_is_paused = _db_one(
            live_server,
            "SELECT status, is_paused FROM media_buys WHERE media_buy_id = %s",
            (media_buy_id,),
        )
        assert db_status == "pending_creatives"
        assert db_is_paused is False


@pytest.mark.asyncio
async def test_inline_creative_failure_does_not_persist_media_buy(docker_services_e2e, live_server, test_auth_token):
    _disable_mock_manual_approval(live_server)
    transport = StreamableHttpTransport(
        url=f"{live_server['mcp']}/mcp/",
        headers={"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"},
    )

    async with Client(transport=transport) as client:
        product_id, pricing_option_id = await _first_product(client)
        start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
        idempotency_key = f"e2e-inline-fail-{uuid.uuid4()}"
        create_req = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=5000.0,
            start_time=start_time,
            end_time=end_time,
            brand={"domain": "testbrand.com"},
            pricing_option_id=pricing_option_id,
        )
        create_req["idempotency_key"] = idempotency_key
        create_req["packages"][0]["creatives"] = [
            {
                "creative_id": f"creative-inline-{uuid.uuid4().hex[:8]}",
                "name": "Invalid Inline Creative Agent",
                "format_id": {
                    "agent_url": "http://unregistered-creative-agent.localtest.me:3092/mcp",
                    "id": "display_300x250",
                },
                "assets": {"banner_image": {"url": "https://placehold.co/300x250.png"}},
                "variants": [],
            }
        ]

        failed = False
        try:
            create_data = parse_tool_result(await client.call_tool("create_media_buy", create_req))
            failed = bool(create_data.get("errors")) or create_data.get("status") == "failed"
        except Exception:
            failed = True
        assert failed

        (side_effect_count,) = _db_one(
            live_server,
            "SELECT COUNT(*) FROM media_buys WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        assert side_effect_count == 0
