"""Regression tests for create_media_buy inline creative atomicity."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
from src.core.database.repositories.creative import CreativeAssignmentRepository, CreativeRepository
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext
from tests.helpers.adcp_factories import create_test_format
from tests.integration.media_buy_helpers import _get_tenant_dict, _make_create_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_identity(tenant: dict[str, Any], principal_id: str) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant["tenant_id"],
        tenant=tenant,
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False),
    )


def _inline_creative(creative_id: str, agent_url: str) -> dict[str, Any]:
    return {
        "creative_id": creative_id,
        "name": f"Inline Creative {creative_id}",
        "format_id": {"agent_url": agent_url, "id": "display_300x250"},
        "assets": {
            "image": {
                "asset_type": "image",
                "url": "https://example.com/ad.png",
                "width": 300,
                "height": 250,
            }
        },
    }


@pytest.fixture(autouse=True)
def mock_creative_format_catalog():
    """Keep inline-creative tests local while preserving production validation paths."""
    mock_formats = {
        "display_image": create_test_format(
            format_id="display_image",
            name="Display Image",
            type="display",
        ),
        "display_300x250": create_test_format(
            format_id="display_300x250",
            name="Display 300x250",
            type="display",
        ),
    }

    def format_spec_side_effect(agent_url, format_id):
        return mock_formats.get(format_id)

    def empty_format_listing(coro):
        coro.close()
        return []

    with (
        patch("src.core.tools.media_buy_create._get_format_spec_sync", side_effect=format_spec_side_effect),
        patch("src.core.tools.creatives._sync.run_async_in_sync_context", side_effect=empty_format_listing),
    ):
        yield


@pytest.mark.asyncio
async def test_invalid_inline_creative_rolls_back_before_media_buy_rows(
    sample_tenant,
    sample_principal,
    sample_products,
    factory_session,
):
    tenant = _get_tenant_dict(sample_tenant["tenant_id"])
    principal_id = sample_principal["principal_id"]
    req = _make_create_request(
        packages=[
            {
                "product_id": sample_products[0],
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
                "creatives": [_inline_creative("issue_671_invalid", "https://unregistered.example.com")],
            }
        ]
    )

    with pytest.raises(AdCPValidationError):
        await _create_media_buy(req, tenant, principal_id)

    factory_session.expire_all()
    assert MediaBuyRepository(factory_session, tenant["tenant_id"]).get_by_principal(principal_id) == []
    assert CreativeRepository(factory_session, tenant["tenant_id"]).get_by_id("issue_671_invalid", principal_id) is None


@pytest.mark.asyncio
async def test_inline_creative_from_advertised_reference_url_is_persisted_and_assigned(
    sample_tenant,
    sample_principal,
    sample_products,
    factory_session,
):
    tenant = _get_tenant_dict(sample_tenant["tenant_id"])
    principal_id = sample_principal["principal_id"]
    creative_id = "issue_671_valid"
    req = _make_create_request(
        packages=[
            {
                "product_id": sample_products[0],
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
                "creatives": [
                    _inline_creative(
                        creative_id,
                        "https://adcontextprotocol.org/agents/formats",
                    )
                ],
            }
        ]
    )

    result = await _create_media_buy(req, tenant, principal_id)

    assert result.status == "completed"
    media_buy_id = result.response.media_buy_id
    assert media_buy_id is not None

    factory_session.expire_all()
    creative_repo = CreativeRepository(factory_session, tenant["tenant_id"])
    assignment_repo = CreativeAssignmentRepository(factory_session, tenant["tenant_id"])
    media_buy_repo = MediaBuyRepository(factory_session, tenant["tenant_id"])
    creative = creative_repo.get_by_id(creative_id, principal_id)
    assert creative is not None
    assert creative.agent_url.rstrip("/") == DEFAULT_CREATIVE_AGENT_URL
    assert [buy.media_buy_id for buy in media_buy_repo.get_by_principal(principal_id)] == [media_buy_id]
    assignments = assignment_repo.get_by_creative(creative_id)
    assert [assignment.media_buy_id for assignment in assignments] == [media_buy_id]


async def _create_media_buy(req, tenant: dict[str, Any], principal_id: str):
    from src.core.tools.media_buy_create import _create_media_buy_impl

    return await _create_media_buy_impl(req=req, identity=_make_identity(tenant, principal_id))
