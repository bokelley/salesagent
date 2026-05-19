"""Tests for InventoryBundleReference — repository + bundle save-time sync + dashboard surface (#485).

The table is a denormalized "is this entity in any bundle?" lookup.
Multi-use is the norm — the same placement can be referenced by many
bundles. Coverage on the dashboard means "of N synced ad units, M
appear in ≥1 bundle." No review/skip semantics.

Covers:

* Repository: ``count_bundled``, ``is_bundled``, ``sync_bundle_references``
* Reconciliation: adding a bundle inserts references; deleting removes
  orphans; reusing the same entity across bundles is a single row
* Dashboard: surface ``ad_units.synced / .bundled`` and
  ``placements.synced / .bundled`` from real data when the tenant is on
  GAM; ``None`` otherwise
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app
from src.core.database.repositories.inventory_bundle_reference import (
    InventoryBundleReferenceRepository,
)
from src.services.inventory_bundle_reference_sync import recompute_bundle_references
from src.services.setup_checklist_service import SetupChecklistService
from tests.factories import (
    GAMInventoryFactory,
    InventoryBundleReferenceFactory,
    InventoryProfileFactory,
    TenantFactory,
)

pytestmark = pytest.mark.requires_db


@pytest.fixture(autouse=True)
def _flask_request_context():
    """``_route_url`` uses Flask url_for; needs a request ctx."""
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False})
    with app.test_request_context():
        yield


class TestCountBundled:
    """``count_bundled`` is the dashboard numerator."""

    def test_empty_tenant_returns_zero(self, factory_session):
        tenant = TenantFactory()

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 0

    def test_count_reflects_existing_rows(self, factory_session):
        tenant = TenantFactory()
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="a")
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="b")
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="c")

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 3

    def test_count_split_by_entity_type(self, factory_session):
        tenant = TenantFactory()
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, entity_type="ad_unit")
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, entity_type="placement")

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 1
        assert repo.count_bundled(adapter="gam", entity_type="placement") == 1

    def test_count_scoped_to_tenant(self, factory_session):
        tenant_a = TenantFactory()
        tenant_b = TenantFactory()
        InventoryBundleReferenceFactory(tenant=tenant_a, tenant_id=tenant_a.tenant_id)
        InventoryBundleReferenceFactory(tenant=tenant_b, tenant_id=tenant_b.tenant_id)

        a_count = InventoryBundleReferenceRepository(factory_session, tenant_a.tenant_id).count_bundled(
            adapter="gam", entity_type="ad_unit"
        )
        b_count = InventoryBundleReferenceRepository(factory_session, tenant_b.tenant_id).count_bundled(
            adapter="gam", entity_type="ad_unit"
        )

        assert a_count == 1
        assert b_count == 1


class TestIsBundled:
    def test_returns_false_when_no_row(self, factory_session):
        tenant = TenantFactory()
        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        assert repo.is_bundled(adapter="gam", entity_type="ad_unit", external_id="abc") is False

    def test_returns_true_when_row_present(self, factory_session):
        tenant = TenantFactory()
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="abc")

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        assert repo.is_bundled(adapter="gam", entity_type="ad_unit", external_id="abc") is True


class TestSyncBundleReferences:
    def test_inserts_new_references(self, factory_session):
        tenant = TenantFactory()
        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        repo.sync_bundle_references(adapter="gam", entity_type="ad_unit", in_bundle_ids=["a", "b"])
        factory_session.flush()

        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 2

    def test_deletes_orphans(self, factory_session):
        tenant = TenantFactory()
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="a")
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="b")

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)
        # Reconcile with only "a" in any bundle now.
        repo.sync_bundle_references(adapter="gam", entity_type="ad_unit", in_bundle_ids=["a"])
        factory_session.flush()

        assert repo.is_bundled(adapter="gam", entity_type="ad_unit", external_id="a") is True
        assert repo.is_bundled(adapter="gam", entity_type="ad_unit", external_id="b") is False

    def test_empty_in_bundle_ids_clears_all(self, factory_session):
        """An adapter+entity_type with no remaining bundle references → all rows deleted."""
        tenant = TenantFactory()
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="a")
        InventoryBundleReferenceFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="b")

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)
        repo.sync_bundle_references(adapter="gam", entity_type="ad_unit", in_bundle_ids=[])
        factory_session.flush()

        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 0

    def test_idempotent_re_sync_same_set(self, factory_session):
        """Reconciling with the same set twice is a no-op count-wise."""
        tenant = TenantFactory()
        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)

        repo.sync_bundle_references(adapter="gam", entity_type="ad_unit", in_bundle_ids=["a", "b"])
        factory_session.flush()
        repo.sync_bundle_references(adapter="gam", entity_type="ad_unit", in_bundle_ids=["a", "b"])
        factory_session.flush()

        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 2

    def test_separate_entity_types_isolated(self, factory_session):
        """Reconciling ad_unit doesn't touch placement rows."""
        tenant = TenantFactory()
        InventoryBundleReferenceFactory(
            tenant=tenant, tenant_id=tenant.tenant_id, entity_type="placement", external_id="p_1"
        )

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)
        repo.sync_bundle_references(adapter="gam", entity_type="ad_unit", in_bundle_ids=[])
        factory_session.flush()

        assert repo.count_bundled(adapter="gam", entity_type="placement") == 1


class TestBundleSaveTimeSync:
    """``recompute_bundle_references`` reconciles the union of all bundles."""

    def test_multi_use_collapses_to_one_row(self, factory_session):
        """The same ad unit referenced by two bundles is still one row.
        That's the whole point — multi-use is the norm."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["shared_unit"], "placements": [], "include_descendants": True},
        )
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["shared_unit"], "placements": [], "include_descendants": True},
        )

        recompute_bundle_references(factory_session, tenant.tenant_id)
        factory_session.flush()

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)
        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 1

    def test_no_op_for_untracked_adapter(self, factory_session):
        tenant = TenantFactory(ad_server="mock")
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["a"], "placements": [], "include_descendants": True},
        )

        recompute_bundle_references(factory_session, tenant.tenant_id)
        factory_session.flush()

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)
        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 0

    def test_deleting_a_bundle_removes_orphan_references(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["ad_1"], "placements": [], "include_descendants": True},
        )
        recompute_bundle_references(factory_session, tenant.tenant_id)
        factory_session.flush()

        factory_session.delete(profile)
        recompute_bundle_references(factory_session, tenant.tenant_id)
        factory_session.flush()

        repo = InventoryBundleReferenceRepository(factory_session, tenant.tenant_id)
        assert repo.count_bundled(adapter="gam", entity_type="ad_unit") == 0


class TestDashboardCoverageSurface:
    """``get_dashboard_jobs()`` surfaces bundle-coverage numbers."""

    def test_gam_tenant_includes_synced_and_bundled_counts(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        # 3 synced ad units, only 1 is in a bundle.
        for inv_id in ("1", "2", "3"):
            GAMInventoryFactory(
                tenant=tenant,
                tenant_id=tenant.tenant_id,
                inventory_type="ad_unit",
                inventory_id=inv_id,
            )
        InventoryBundleReferenceFactory(
            tenant=tenant, tenant_id=tenant.tenant_id, entity_type="ad_unit", external_id="1"
        )

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        bundles_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "bundles")
        cov = bundles_sub["coverage"]
        assert cov["adapter"] == "gam"
        assert cov["ad_units"]["synced"] == 3
        assert cov["ad_units"]["bundled"] == 1
        assert cov["has_synced_inventory"] is True

    def test_non_gam_tenant_has_no_coverage(self, factory_session):
        tenant = TenantFactory(ad_server="mock")

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        bundles_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "bundles")
        assert bundles_sub["coverage"] is None

    def test_no_synced_inventory_falls_back_to_placeholder(self, factory_session):
        """GAM tenant with no synced inventory yet: has_synced_inventory=False
        so the widget shows the placeholder hint."""
        tenant = TenantFactory(ad_server="google_ad_manager")

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        bundles_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "bundles")
        cov = bundles_sub["coverage"]
        assert cov is not None
        assert cov["has_synced_inventory"] is False

    def test_signals_coverage_is_none_pending_486(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        signals_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "signals")
        assert signals_sub["coverage"] is None
