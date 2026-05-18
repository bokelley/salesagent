"""Tests for SetupChecklistService.get_capability_ladder().

Covers the seller capability ladder introduced in #471:

* L1 Wholesale: ad server + ≥1 inventory bundle
* L2 Wholesale + Signals: L1 + ≥1 signal profile
* L3 Composed Products: L2 + ≥1 product with inventory_profile_id AND signal_targeting_allowed
* Embedded tenants cap at L2 (composition lives upstream in the storefront)
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app
from src.services.setup_checklist_service import SetupChecklistService
from tests.factories import (
    InventoryProfileFactory,
    ProductFactory,
    TenantFactory,
    TenantSignalFactory,
)

pytestmark = pytest.mark.requires_db


@pytest.fixture(autouse=True)
def _testing_env(monkeypatch):
    """Mock adapter only counts as configured when ADCP_TESTING=true; we use
    mock-adapter tenants throughout these tests to keep the ad-server check
    boolean tractable."""
    monkeypatch.setenv("ADCP_TESTING", "true")


@pytest.fixture(autouse=True)
def _flask_request_context():
    """SetupChecklistService._route_url uses url_for(), which needs a
    request context — these tests invoke the service directly."""
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False})
    with app.test_request_context():
        yield


def _rung(ladder: dict, key: str) -> dict:
    """Pull the named rung out of the ladder result."""
    for rung in ladder["rungs"]:
        if rung["key"] == key:
            return rung
    raise AssertionError(f"Rung {key!r} missing from ladder result")


class TestCapabilityLadderTier0:
    """Tier 0 = nothing salable yet. Default tenant has only a mock ad server."""

    def test_brand_new_tenant_is_tier_0(self, factory_session):
        # Stripping the ad server is the easiest way to land at tier 0 — even
        # with a mock adapter and ADCP_TESTING=true, the bundle/signal counts
        # are zero so wholesale is locked.
        tenant = TenantFactory(ad_server=None)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 0
        assert _rung(ladder, "wholesale")["unlocked"] is False
        assert _rung(ladder, "signals")["unlocked"] is False
        assert _rung(ladder, "composed_products")["unlocked"] is False

    def test_tier_0_lists_wholesale_blockers(self, factory_session):
        tenant = TenantFactory(ad_server=None)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        blockers = _rung(ladder, "wholesale")["blockers"]
        assert "Connect an ad server" in blockers
        assert "Author at least one inventory bundle" in blockers


class TestCapabilityLadderTier1:
    """Tier 1 = Wholesale unlocked. Mock adapter + ≥1 inventory bundle."""

    def test_inventory_bundle_unlocks_wholesale(self, factory_session):
        tenant = TenantFactory()  # ad_server="mock", ADCP_TESTING=true → configured
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 1
        assert _rung(ladder, "wholesale")["unlocked"] is True
        assert _rung(ladder, "signals")["unlocked"] is False
        assert _rung(ladder, "composed_products")["unlocked"] is False

    def test_wholesale_count_surfaces(self, factory_session):
        tenant = TenantFactory()
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert _rung(ladder, "wholesale")["counts"]["inventory_bundles"] == 2


class TestCapabilityLadderTier2:
    """Tier 2 = Wholesale + Signals. L1 + ≥1 signal profile."""

    def test_signal_profile_unlocks_tier_2(self, factory_session):
        tenant = TenantFactory()
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 2
        assert _rung(ladder, "signals")["unlocked"] is True
        assert _rung(ladder, "composed_products")["unlocked"] is False

    def test_signal_without_bundle_keeps_tier_0(self, factory_session):
        """Signal alone doesn't unlock T2 — wholesale is the prerequisite."""
        tenant = TenantFactory()
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 0
        assert _rung(ladder, "wholesale")["unlocked"] is False
        assert _rung(ladder, "signals")["unlocked"] is False
        # T2 surfaces the wholesale prereq as the blocker
        assert "Unlock Wholesale first" in _rung(ladder, "signals")["blockers"]


class TestCapabilityLadderTier3:
    """Tier 3 = Composed Products. L2 + product with bundle + signal_targeting_allowed."""

    def test_composed_product_unlocks_tier_3(self, factory_session):
        tenant = TenantFactory()
        bundle = InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_profile_id=bundle.id,
            signal_targeting_allowed=True,
        )

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 3
        assert _rung(ladder, "composed_products")["unlocked"] is True

    def test_product_without_signal_targeting_does_not_unlock_t3(self, factory_session):
        """L3 requires signal_targeting_allowed=True — a product with only a
        bundle attached is a wholesale offering, not a composed product."""
        tenant = TenantFactory()
        bundle = InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_profile_id=bundle.id,
            signal_targeting_allowed=False,
        )

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 2
        assert _rung(ladder, "composed_products")["unlocked"] is False
        assert _rung(ladder, "composed_products")["counts"]["composed_products"] == 0

    def test_product_without_bundle_does_not_unlock_t3(self, factory_session):
        """L3 requires inventory_profile_id IS NOT NULL — a product that
        opts into signal targeting but doesn't attach a bundle doesn't count."""
        tenant = TenantFactory()
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_profile_id=None,
            signal_targeting_allowed=True,
        )

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 2
        assert _rung(ladder, "composed_products")["unlocked"] is False


@pytest.fixture
def embedded_factory_session(factory_session):
    """Factory session that bypasses the embedded-tenant write guard.

    Embedded tenants are normally platform-managed (Tenant Management API
    only); these tests need to write them directly via factories. The
    ``management_api_caller`` flag is the documented escape hatch (see
    ``tests/integration/_embedded_helpers.py``).
    """
    factory_session.info["management_api_caller"] = True
    yield factory_session


class TestCapabilityLadderEmbeddedCap:
    """Embedded tenants cap at L2: composition lives upstream in the storefront."""

    def test_embedded_max_tier_is_2(self, embedded_factory_session):
        tenant = TenantFactory(is_embedded=True)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["max_unlockable_tier"] == 2
        assert ladder["is_embedded"] is True

    def test_open_instance_max_tier_is_3(self, factory_session):
        tenant = TenantFactory(is_embedded=False)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["max_unlockable_tier"] == 3
        assert ladder["is_embedded"] is False

    def test_embedded_current_tier_capped_at_max(self, embedded_factory_session):
        """If an embedded tenant somehow has a composed product (legacy data
        or a storefront write), ``current_tier`` must not exceed
        ``max_unlockable_tier`` — otherwise the widget shows the contradictory
        'L3 · 2/2 tiers unlocked'.
        """
        tenant = TenantFactory(is_embedded=True)
        bundle = InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_profile_id=bundle.id,
            signal_targeting_allowed=True,
        )

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["max_unlockable_tier"] == 2
        assert ladder["current_tier"] == 2  # capped, not 3

    def test_embedded_hides_composed_products_rung(self, embedded_factory_session):
        tenant = TenantFactory(is_embedded=True)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert _rung(ladder, "composed_products")["hidden"] is True
        # Wholesale + signals stay visible for embedded
        assert _rung(ladder, "wholesale")["hidden"] is False
        assert _rung(ladder, "signals")["hidden"] is False


class TestCapabilityLadderAdServer:
    """Mock adapter only counts as configured when ADCP_TESTING=true."""

    def test_mock_adapter_without_testing_flag_locks_wholesale(self, factory_session, monkeypatch):
        monkeypatch.delenv("ADCP_TESTING", raising=False)
        tenant = TenantFactory(ad_server="mock")
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        ladder = SetupChecklistService(tenant.tenant_id).get_capability_ladder()

        assert ladder["current_tier"] == 0
        assert "Connect an ad server" in _rung(ladder, "wholesale")["blockers"]

    def test_unknown_tenant_raises(self, factory_session):
        with pytest.raises(ValueError, match="Tenant nonexistent not found"):
            SetupChecklistService("nonexistent").get_capability_ladder()
