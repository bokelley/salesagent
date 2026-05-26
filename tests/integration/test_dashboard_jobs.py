"""Tests for SetupChecklistService.get_dashboard_jobs().

Covers the three-job dashboard widget introduced in #471 — the operator's
persistent workbench, not a setup wizard:

* Discovery: bundles + signals (the primary job)
* Composition: products (hidden when storefront-owned)
* Delivery: light link card

Always shown — jobs are ongoing. Distinct from the hygiene gate.
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
def _flask_request_context():
    """``_route_url`` uses Flask ``url_for`` — needs a request context."""
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False})
    with app.test_request_context():
        yield


def _job(result: dict, key: str) -> dict:
    for job in result["jobs"]:
        if job["key"] == key:
            return job
    raise AssertionError(f"Job {key!r} missing")


def _sub(job: dict, key: str) -> dict:
    for sub in job.get("sub_items", []):
        if sub["key"] == key:
            return sub
    raise AssertionError(f"Sub-item {key!r} missing from job {job['key']!r}")


class TestThreeJobsShape:
    """The widget surfaces jobs according to embedded capability ownership."""

    def test_open_instance_returns_all_three_jobs(self, factory_session):
        tenant = TenantFactory(is_embedded=False)

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        job_keys = [j["key"] for j in result["jobs"]]
        assert job_keys == ["discovery", "composition", "delivery"]
        assert result["is_embedded"] is False

    def test_embedded_publisher_owned_keeps_all_three_jobs(self, monkeypatch, factory_session):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)
        # Need management_api_caller flag for direct embedded-tenant inserts.
        factory_session.info["management_api_caller"] = True
        tenant = TenantFactory(is_embedded=True)

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        job_keys = [j["key"] for j in result["jobs"]]
        assert job_keys == ["discovery", "composition", "delivery"]
        assert result["is_embedded"] is True

    def test_embedded_storefront_owned_hides_composition(self, monkeypatch, factory_session):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"compose_products":"storefront"}')
        # Need management_api_caller flag for direct embedded-tenant inserts.
        factory_session.info["management_api_caller"] = True
        tenant = TenantFactory(is_embedded=True)

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        job_keys = [j["key"] for j in result["jobs"]]
        assert job_keys == ["discovery", "delivery"]
        assert result["is_embedded"] is True


class TestDiscoveryJob:
    """Discovery surfaces bundle + signal counts. Coverage analytics come
    in follow-up work (#471 explicitly punts that)."""

    def test_empty_tenant_shows_zero_counts(self, factory_session):
        tenant = TenantFactory()

        discovery = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "discovery")

        assert _sub(discovery, "bundles")["count"] == 0
        assert _sub(discovery, "bundles")["started"] is False
        assert _sub(discovery, "signals")["count"] == 0
        assert _sub(discovery, "signals")["started"] is False

    def test_bundles_count_reflects_inventory_profiles(self, factory_session):
        tenant = TenantFactory()
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        discovery = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "discovery")

        assert _sub(discovery, "bundles")["count"] == 2
        assert _sub(discovery, "bundles")["started"] is True

    def test_signals_count_reflects_tenant_signals(self, factory_session):
        tenant = TenantFactory()
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        discovery = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "discovery")

        assert _sub(discovery, "signals")["count"] == 1
        assert _sub(discovery, "signals")["started"] is True

    def test_action_label_switches_with_state(self, factory_session):
        """Empty state shows 'Author', populated shows 'Review' — small
        but real UX signal that this is ongoing work, not a one-time setup."""
        tenant = TenantFactory()

        # Empty
        discovery = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "discovery")
        assert _sub(discovery, "bundles")["action_label"] == "Author bundles"
        assert _sub(discovery, "signals")["action_label"] == "Author signals"

        # Populated
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        discovery = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "discovery")
        assert _sub(discovery, "bundles")["action_label"] == "Review bundles"
        assert _sub(discovery, "signals")["action_label"] == "Review signals"


class TestCompositionJob:
    """Composition surfaces product count when publisher-owned."""

    def test_open_instance_shows_product_count(self, factory_session):
        tenant = TenantFactory(is_embedded=False)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        composition = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "composition")

        assert composition["count"] == 1
        assert composition["count_label"] == "product"

    def test_count_label_pluralizes(self, factory_session):
        tenant = TenantFactory(is_embedded=False)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        composition = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "composition")

        assert composition["count"] == 2
        assert composition["count_label"] == "products"

    def test_empty_state_action_label(self, factory_session):
        tenant = TenantFactory(is_embedded=False)

        composition = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "composition")

        assert composition["action_label"] == "Compose a product"

    def test_populated_state_action_label(self, factory_session):
        tenant = TenantFactory(is_embedded=False)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        composition = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "composition")

        assert composition["action_label"] == "Manage products"


class TestDeliveryJob:
    """Delivery is a light card — the pipeline strip below holds detail."""

    def test_delivery_action_url_resolves_to_reporting(self, factory_session):
        """The Delivery CTA must point at the reporting page (not a fallback
        None or some other route)."""
        tenant = TenantFactory(is_embedded=False)

        delivery = _job(SetupChecklistService(tenant.tenant_id).get_dashboard_jobs(), "delivery")

        assert delivery["name"] == "Delivery"
        assert delivery["action_url"] is not None
        assert delivery["action_url"].endswith("/reporting")


class TestEdgeCases:
    def test_unknown_tenant_raises(self, factory_session):
        with pytest.raises(ValueError, match="Tenant nonexistent not found"):
            SetupChecklistService("nonexistent").get_dashboard_jobs()
