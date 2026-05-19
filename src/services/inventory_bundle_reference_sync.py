"""Keeps ``InventoryBundleReference`` in lockstep with ``InventoryProfile`` mutations.

Hooked into the inventory_profiles blueprint after any create / edit /
delete. The bundle save and the reference-table reconcile run in the
same session so either both commit or both roll back.

The reconcile is intentionally full-tenant (not delta-aware): bundle
configs are JSON blobs, so we can't compute the delta cheaply, and
running a full reconcile over all of a tenant's bundles is fine even at
scale — bundle counts per tenant are small (tens, not thousands).

For #485 GAM is the only adapter consuming the data. The adapter is
read from ``tenant.ad_server``. FreeWheel / SpringServe land when their
inventory sync surfaces do.
"""

from __future__ import annotations

import logging
from typing import Final

from sqlalchemy.orm import Session

from src.core.database.repositories.inventory_bundle_reference import (
    InventoryBundleReferenceRepository,
)
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository

logger = logging.getLogger(__name__)

# Adapter slugs we know how to track inventory for today. Tenants on other
# ad servers won't have InventoryBundleReference rows synced — the
# dashboard skips coverage for them rather than guessing.
_TRACKED_ADAPTERS: Final[frozenset[str]] = frozenset({"google_ad_manager", "gam"})


def recompute_bundle_references(session: Session, tenant_id: str) -> None:
    """Reconcile the ``InventoryBundleReference`` set for a tenant.

    Walks every ``InventoryProfile`` for the tenant, takes the union of
    ``inventory_config['ad_units']`` and ``inventory_config['placements']``,
    and tells the reference repository which ids are currently in a
    bundle. The repo inserts new references and deletes orphans.

    Call this *after* the bundle mutation is staged in the session
    (``session.add`` / ``session.delete``) and *before* the commit, so the
    two writes share a transaction.

    Silently no-ops if the tenant's adapter isn't tracked yet.
    """
    tenant = TenantConfigRepository(session, tenant_id).get_tenant()
    if tenant is None:
        # Tenant deleted mid-flight or unknown — nothing to reconcile.
        return
    adapter = tenant.ad_server
    if adapter not in _TRACKED_ADAPTERS:
        return
    adapter_slug = "gam"  # Canonicalize for the table; ``google_ad_manager`` is the tenant column.

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
    repo.sync_bundle_references(adapter=adapter_slug, entity_type="ad_unit", in_bundle_ids=ad_unit_ids)
    repo.sync_bundle_references(adapter=adapter_slug, entity_type="placement", in_bundle_ids=placement_ids)

    logger.info(
        "Reconciled inventory_bundle_reference for tenant=%s: %d ad_units, %d placements bundled",
        tenant_id,
        len(ad_unit_ids),
        len(placement_ids),
    )
