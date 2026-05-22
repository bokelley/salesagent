"""Keeps ``InventoryBundleReference`` in lockstep with ``InventoryProfile`` mutations.

Hooked into the inventory_profiles blueprint after any create / edit /
delete. The bundle save and the reference-table reconcile run in the
same session so either both commit or both roll back.

The reconcile is intentionally full-tenant (not delta-aware): bundle
configs are JSON blobs, so we can't compute the delta cheaply, and
running a full reconcile over all of a tenant's bundles is fine even at
scale — bundle counts per tenant are small (tens, not thousands).

Adapter resolution goes through the bundle-adapter registry (#521). GAM
is the only adapter that participates in references today; FW + SS are
stubs (no synced inventory ⇒ no references). Adding a fourth adapter
that wants references means registering it in ``bundle_adapter``; this
service iterates whatever's there.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.core.database.repositories.inventory_bundle_reference import (
    InventoryBundleReferenceRepository,
)
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.services.bundle_adapter import adapter_for_tenant

logger = logging.getLogger(__name__)


def recompute_bundle_references(session: Session, tenant_id: str) -> None:
    """Reconcile the ``InventoryBundleReference`` set for a tenant.

    Walks every ``InventoryProfile`` for the tenant, takes the union of
    ``inventory_config['ad_units']`` and ``inventory_config['placements']``,
    and tells the reference repository which ids are currently in a
    bundle. The repo inserts new references and deletes orphans.

    Call this *after* the bundle mutation is staged in the session
    (``session.add`` / ``session.delete``) and *before* the commit, so the
    two writes share a transaction.

    Silently no-ops for tenants on an ad server no bundle adapter has
    claimed (the bundle UI degrades to "no coverage" for them anyway).
    """
    tenant = TenantConfigRepository(session, tenant_id).get_tenant()
    if tenant is None:
        # Tenant deleted mid-flight or unknown — nothing to reconcile.
        return
    adapter = adapter_for_tenant(tenant.ad_server)
    if adapter is None:
        return

    # Pending session writes must be visible to the repository read below.
    session.flush()

    bundles = InventoryProfileRepository(session, tenant_id).list_all()

    ad_unit_ids: set[str] = set()
    placement_ids: set[str] = set()
    for bundle in bundles:
        config = bundle.inventory_config or {}
        for raw in config.get("ad_units", []) or []:
            ad_unit_ids.add(str(raw))
        for raw in config.get("placements", []) or []:
            placement_ids.add(str(raw))

    repo = InventoryBundleReferenceRepository(session, tenant_id)
    repo.sync_bundle_references(adapter=adapter.adapter_id, entity_type="ad_unit", in_bundle_ids=ad_unit_ids)
    repo.sync_bundle_references(adapter=adapter.adapter_id, entity_type="placement", in_bundle_ids=placement_ids)

    logger.info(
        "Reconciled inventory_bundle_reference for tenant=%s (adapter=%s): %d ad_units, %d placements bundled",
        tenant_id,
        adapter.adapter_id,
        len(ad_unit_ids),
        len(placement_ids),
    )
