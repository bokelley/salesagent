"""Tests for SetupChecklistService.get_seller_setup_progress().

Covers the seller setup progress widget introduced in #471. Frames the
dashboard around buyer demand, not tier progression:

* Step 1 — Catalog: inventory bundles + signal profiles (raw materials)
* Step 2 — Products: composed from catalog. Open-instance only;
  embedded tenants skip this step (storefront composes upstream).

The widget is distinct from the hygiene checklist — it doesn't gate on
SSO/AAO/currency/ad-server. It hides when every relevant step is done.
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


def _step(setup: dict, key: str) -> dict:
    for step in setup["steps"]:
        if step["key"] == key:
            return step
    raise AssertionError(f"Step {key!r} missing from progress result")


def _sub(step: dict, key: str) -> dict:
    for sub in step.get("sub_items", []):
        if sub["key"] == key:
            return sub
    raise AssertionError(f"Sub-item {key!r} missing from step {step['key']!r}")


class TestCatalogStep:
    """Step 1 — Catalog: bundles + signals as sub-items."""

    def test_empty_tenant_has_incomplete_catalog(self, factory_session):
        tenant = TenantFactory()

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        catalog = _step(setup, "catalog")
        assert catalog["complete"] is False
        assert _sub(catalog, "bundles")["complete"] is False
        assert _sub(catalog, "signals")["complete"] is False

    def test_bundle_only_does_not_complete_catalog(self, factory_session):
        tenant = TenantFactory()
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        catalog = _step(setup, "catalog")
        assert catalog["complete"] is False
        assert _sub(catalog, "bundles")["complete"] is True
        assert _sub(catalog, "bundles")["count"] == 1
        assert _sub(catalog, "signals")["complete"] is False
        assert _sub(catalog, "signals")["blocker"] == "Author at least one signal profile"

    def test_signal_only_does_not_complete_catalog(self, factory_session):
        tenant = TenantFactory()
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        catalog = _step(setup, "catalog")
        assert catalog["complete"] is False
        assert _sub(catalog, "bundles")["complete"] is False
        assert _sub(catalog, "signals")["complete"] is True

    def test_both_bundle_and_signal_complete_catalog(self, factory_session):
        tenant = TenantFactory()
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        assert _step(setup, "catalog")["complete"] is True


class TestProductsStep:
    """Step 2 — Products: open-instance only. Static products today;
    dynamic composition is the direction but not built here."""

    def test_open_instance_has_products_step(self, factory_session):
        tenant = TenantFactory(is_embedded=False)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        assert _step(setup, "products")["complete"] is False
        assert _step(setup, "products")["count"] == 0

    def test_one_product_completes_products_step(self, factory_session):
        """Any product counts — we don't gate on bundle+signal attachment.
        Manual product CRUD is the current path; the widget reflects state,
        not a recipe for what makes a 'good' product."""
        tenant = TenantFactory(is_embedded=False)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        assert _step(setup, "products")["complete"] is True
        assert _step(setup, "products")["count"] == 1


class TestEmbeddedSkipsProducts:
    """Embedded tenants skip Step 2 entirely: composition runs upstream."""

    @pytest.fixture
    def embedded_factory_session(self, factory_session):
        """Bypass the embedded-tenant write guard for direct factory inserts."""
        factory_session.info["management_api_caller"] = True
        yield factory_session

    def test_embedded_steps_omit_products(self, embedded_factory_session):
        tenant = TenantFactory(is_embedded=True)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        step_keys = [s["key"] for s in setup["steps"]]
        assert "catalog" in step_keys
        assert "products" not in step_keys
        assert setup["is_embedded"] is True

    def test_embedded_all_complete_when_catalog_done(self, embedded_factory_session):
        """Embedded tenant with bundles + signals is done — no Step 2
        required. The widget should hide."""
        tenant = TenantFactory(is_embedded=True)
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        assert setup["all_complete"] is True

    def test_embedded_with_existing_products_still_skips_step(self, embedded_factory_session):
        """Even if an embedded tenant has products (legacy or storefront-
        written), the dashboard doesn't surface a Products step — the
        storefront owns composition."""
        tenant = TenantFactory(is_embedded=True)
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        assert "products" not in [s["key"] for s in setup["steps"]]
        assert setup["all_complete"] is True


class TestAllComplete:
    """``all_complete`` controls whether the widget renders. Open-instance
    needs catalog + products; embedded needs just catalog."""

    def test_open_instance_needs_catalog_and_products(self, factory_session):
        tenant = TenantFactory(is_embedded=False)
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        # Catalog done, products not → not all complete
        assert _step(setup, "catalog")["complete"] is True
        assert _step(setup, "products")["complete"] is False
        assert setup["all_complete"] is False

    def test_open_instance_all_complete(self, factory_session):
        tenant = TenantFactory(is_embedded=False)
        InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        TenantSignalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        setup = SetupChecklistService(tenant.tenant_id).get_seller_setup_progress()

        assert setup["all_complete"] is True


class TestEdgeCases:
    def test_unknown_tenant_raises(self, factory_session):
        with pytest.raises(ValueError, match="Tenant nonexistent not found"):
            SetupChecklistService("nonexistent").get_seller_setup_progress()
