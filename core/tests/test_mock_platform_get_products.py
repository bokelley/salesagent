"""Unit test: MockSellerPlatform.get_products reads tenant-scoped product rows.

This is the M1 entry-point test. Validates:
- AccountStore resolves explicit ``"<tenant>:<account>"`` refs.
- get_products filters by tenant_id from ``ctx.account.metadata``.
- Wire projection conforms to AdCP get_products response shape.

DB access is mocked at the session level so this stays a unit test.
The end-to-end storyboard run lands in
``core/tests/storyboards/test_media_buy_seller.py`` (M1 second half).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adcp.decisioning.context import RequestContext

from core.platforms.mock import MockSellerPlatform


def _fake_product(**overrides):
    p = MagicMock()
    p.tenant_id = overrides.get("tenant_id", "test-tenant")
    p.product_id = overrides.get("product_id", "prod1")
    p.name = overrides.get("name", "Test Product")
    p.description = overrides.get("description", "A product for testing")
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
def mock_session_with_product():
    """Patch get_db_session in BOTH stores.accounts and platforms.mock."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    # AccountStore tenant-existence check returns active tenant
    active_tenant = MagicMock(is_active=True)
    # get_products query returns one product
    session.scalars.return_value.all.return_value = [_fake_product()]
    session.scalars.return_value.first.return_value = active_tenant

    with patch("core.platforms.mock.get_db_session", return_value=session), patch(
        "core.stores.accounts.get_db_session", return_value=session
    ):
        yield session


def test_get_products_returns_tenant_scoped_products(mock_session_with_product):
    platform = MockSellerPlatform()
    account = platform.accounts.resolve(ref={"account_id": "test-tenant:demo"})
    ctx = RequestContext(account=account, request_id="req-1")

    result = platform.get_products(req={}, ctx=ctx)

    assert "products" in result
    assert len(result["products"]) == 1
    product = result["products"][0]
    assert product["product_id"] == "prod1"
    assert product["delivery_type"] == "non_guaranteed"
    assert product["pricing_options"][0]["pricing_model"] == "cpm"
    assert product["format_ids"][0]["id"] == "display_300x250"


def test_get_products_rejects_missing_tenant_metadata(mock_session_with_product):
    """If ctx.account has no tenant_id metadata, raise ACCOUNT_NOT_FOUND."""
    from adcp.decisioning import AdcpError
    from adcp.decisioning.types import Account

    platform = MockSellerPlatform()
    bad_account = Account(id="anonymous", metadata={})
    ctx = RequestContext(account=bad_account, request_id="req-2")

    with pytest.raises(AdcpError) as exc_info:
        platform.get_products(req={}, ctx=ctx)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"


def test_account_store_rejects_unknown_tenant():
    """AccountStore raises ACCOUNT_NOT_FOUND for tenants not in the DB."""
    from adcp.decisioning import AdcpError

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalars.return_value.first.return_value = None  # no row

    with patch("core.stores.accounts.get_db_session", return_value=session):
        platform = MockSellerPlatform()
        with pytest.raises(AdcpError) as exc_info:
            platform.accounts.resolve(ref={"account_id": "nonexistent-tenant:demo"})

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"


def test_publisher_properties_uses_tags_when_present(mock_session_with_product, monkeypatch):
    """Verify the property_tags branch of _publisher_properties_from_row."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalars.return_value.all.return_value = [
        _fake_product(property_tags=["sports", "premium"])
    ]
    session.scalars.return_value.first.return_value = MagicMock(is_active=True)

    monkeypatch.setattr("core.platforms.mock.get_db_session", lambda: session)
    monkeypatch.setattr("core.stores.accounts.get_db_session", lambda: session)

    platform = MockSellerPlatform()
    account = platform.accounts.resolve(ref={"account_id": "test-tenant:demo"})
    ctx = RequestContext(account=account, request_id="req-3")

    result = platform.get_products(req={}, ctx=ctx)
    pubs = result["products"][0]["publisher_properties"]
    assert len(pubs) == 2
    assert all(p["selection_type"] == "by_tag" for p in pubs)
    assert {p["publisher_tag"] for p in pubs} == {"sports", "premium"}
