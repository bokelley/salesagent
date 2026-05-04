"""End-to-end M1 test: get_products dispatched via the framework's PlatformHandler.

This is the M1 milestone test. It validates the full path:

  GetProductsRequest (typed Pydantic, wire shape)
      ↓
  PlatformHandler.get_products()             — framework's dispatch shim
      ↓
  MockSellerPlatform.get_products()          — adopter business logic
      ↓
  ProductRow ORM rows (mocked)               — salesagent ORM bridge
      ↓
  AdCP get_products response (dict)          — wire-compatible result

This is the lowest layer that exercises the framework's real dispatch
machinery. The MCP/A2A transport layer above it is the framework's
responsibility — once this works, ``serve()`` works.

DB is mocked at the session level. M2 lands a docker-compose-backed
storyboard run that drives this through real HTTP.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from adcp.decisioning.serve import create_adcp_server_from_platform
from adcp.server.base import ToolContext
from adcp.testing import make_request_context  # noqa: F401 — shows the SDK now ships this helper
from adcp.types import GetProductsRequest

from core.platforms.mock import MockSellerPlatform


def _fake_product_row(**overrides):
    p = MagicMock()
    p.tenant_id = overrides.get("tenant_id", "demo-tenant")
    p.product_id = overrides.get("product_id", "demo-product")
    p.name = overrides.get("name", "Demo Product")
    p.description = overrides.get("description", "A demo product")
    p.delivery_type = overrides.get("delivery_type", "non_guaranteed")
    p.format_ids = overrides.get(
        "format_ids",
        [{"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250"}],
    )
    p.delivery_measurement = overrides.get("delivery_measurement", {"provider": "publisher"})
    p.properties = overrides.get("properties")
    p.property_ids = overrides.get("property_ids")
    p.property_tags = overrides.get("property_tags")
    return p


@pytest.fixture
def mocked_db():
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalars.return_value.all.return_value = [_fake_product_row()]
    session.scalars.return_value.first.return_value = MagicMock(is_active=True)
    with patch("core.platforms.mock.get_db_session", return_value=session), patch(
        "core.stores.accounts.get_db_session", return_value=session
    ):
        yield session


def test_get_products_dispatches_through_framework_handler(mocked_db):
    """A real PlatformHandler dispatch returns a wire-compatible response."""
    platform = MockSellerPlatform()
    handler, _executor, _registry = create_adcp_server_from_platform(
        platform, auto_emit_completion_webhooks=False
    )

    req = GetProductsRequest(
        account={"account_id": "demo-tenant:demo"},
        promoted_offering="shoes",
        buying_mode="brief",
        brief="display ads for sneakers",
    )
    ctx = ToolContext()

    result = asyncio.run(handler.get_products(req, ctx))

    assert isinstance(result, dict)
    assert "products" in result
    assert len(result["products"]) == 1

    product = result["products"][0]
    assert product["product_id"] == "demo-product"
    assert product["delivery_type"] == "non_guaranteed"
    assert product["pricing_options"][0]["pricing_model"] == "cpm"
    # Framework requires reporting_capabilities on every product
    assert "reporting_capabilities" in product
    # Wire shape: format_ids must be FormatId-shaped dicts
    assert product["format_ids"][0]["agent_url"].startswith("https://")
