"""Adapter framework for inventory-bundle authoring (#521).

The bundle list page, edit page, Reuse page, and the
``inventory_bundle_reference`` recompute were all GAM-shaped. This module
factors the GAM-specific bits behind a ``BundleInventoryAdapter`` protocol
so each ad server (GAM today, FreeWheel + SpringServe next) plugs in
without touching the UI or service layer.

The blueprint calls dispatch through ``get_adapter(adapter_id)`` and
``iter_adapters()`` to ask each adapter for coverage counts, unbundled
items, name resolution, seed suggestions, etc. The protocol returns
adapter-agnostic ``BundleInventoryRow`` records so templates don't need
to know which adapter owns a row.

GAM is fully implemented. FW + SS are honest stubs — they return empty
results today (their inventory sync surfaces don't carry the same data
shape yet) but ship with their canonical labels + vocab so a tenant on
either of them sees the right copy in the page header.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalized row shape — what the blueprint and templates receive
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleInventoryRow:
    """One ad_unit or placement, adapter-agnostic.

    The blueprint reads ``external_id``, ``name``, ``entity_type``, and
    ``meta`` directly. ``raw`` carries adapter-specific extras (sizes for
    GAM ad units, etc.) for templates that opt in.
    """

    external_id: str
    name: str
    entity_type: str  # "ad_unit" | "placement"
    meta: str  # human-readable one-liner: "editorial · 300×250 · 2.1k imps/day"
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BundleInventoryAdapter(Protocol):
    """Read-only inventory surface that the bundle UI dispatches through.

    Implementations live next to each adapter's sync code. Register them
    at import time via :func:`register_adapter`.
    """

    adapter_id: str  # canonical id used in URL params, model columns, etc.
    label: str  # "Google Ad Manager"
    vocab: dict[str, str]  # {"primary": "ad units", "secondary": "placements"}
    matches_tenant_ad_server: set[str]  # values of ``tenant.ad_server`` to claim

    def has_synced_inventory(self, session: Session, tenant_id: str) -> bool: ...

    def count_inventory(self, session: Session, tenant_id: str, entity_type: str) -> int: ...

    def list_inventory_by_ids(
        self, session: Session, tenant_id: str, entity_type: str, ids: list[str]
    ) -> list[BundleInventoryRow]: ...

    def list_unbundled(
        self,
        session: Session,
        tenant_id: str,
        bundled_ids_by_type: dict[str, set[str]],
        limit: int,
    ) -> list[BundleInventoryRow]: ...

    def list_top_level_placements(self, session: Session, tenant_id: str, limit: int) -> list[BundleInventoryRow]: ...

    def find_inventory_item(
        self, session: Session, tenant_id: str, entity_type: str, external_id: str
    ) -> BundleInventoryRow | None: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_ADAPTERS: dict[str, BundleInventoryAdapter] = {}


def register_adapter(adapter: BundleInventoryAdapter) -> None:
    """Register an adapter. Last write wins — useful for test overrides."""
    _ADAPTERS[adapter.adapter_id] = adapter


def get_adapter(adapter_id: str) -> BundleInventoryAdapter | None:
    """Look up a registered adapter by id, or ``None`` if unknown."""
    return _ADAPTERS.get(adapter_id)


def iter_adapters() -> list[BundleInventoryAdapter]:
    """All registered adapters, ordered by id for determinism."""
    return [_ADAPTERS[k] for k in sorted(_ADAPTERS.keys())]


def adapter_for_tenant(tenant_ad_server: str | None) -> BundleInventoryAdapter | None:
    """Resolve the adapter that claims this tenant's ``ad_server`` column.

    Returns ``None`` for tenants on ad servers no adapter has registered
    for (the bundle UI degrades to "no synced inventory yet" copy).
    """
    if not tenant_ad_server:
        return None
    for adapter in iter_adapters():
        if tenant_ad_server in adapter.matches_tenant_ad_server:
            return adapter
    return None


def _format_inventory_meta(name: str, path: list | None, status: str | None) -> str:
    """One-liner shared across adapter implementations.

    Mirrors the old ``_format_inventory_meta`` in the inventory_profiles
    blueprint — extracted here so each adapter renders meta consistently.
    """
    parts = []
    if path:
        parts.append(" › ".join(path[-3:]))
    if status and status.lower() != "active":
        parts.append(status.lower())
    return " · ".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# GAM adapter
# ---------------------------------------------------------------------------


class _GAMAdapter:
    """Google Ad Manager bundle-inventory adapter (#521).

    Delegates to ``GAMSyncRepository`` for all reads. The repository owns
    the SQL; this adapter just normalizes shape.
    """

    adapter_id = "gam"
    label = "Google Ad Manager"
    vocab = {"primary": "ad units", "secondary": "placements"}
    matches_tenant_ad_server = {"google_ad_manager", "gam"}

    def _row_from_gam_inventory(self, row) -> BundleInventoryRow:
        return BundleInventoryRow(
            external_id=row.inventory_id,
            name=row.name,
            entity_type=row.inventory_type,
            meta=_format_inventory_meta(row.name, row.path, row.status),
            raw={"path": row.path, "status": row.status, "metadata": row.inventory_metadata},
        )

    def has_synced_inventory(self, session: Session, tenant_id: str) -> bool:
        return (
            self.count_inventory(session, tenant_id, "ad_unit") + self.count_inventory(session, tenant_id, "placement")
        ) > 0

    def count_inventory(self, session: Session, tenant_id: str, entity_type: str) -> int:
        from src.core.database.repositories.gam_sync import GAMSyncRepository

        return GAMSyncRepository(session, tenant_id).count_inventory(entity_type)

    def list_inventory_by_ids(
        self, session: Session, tenant_id: str, entity_type: str, ids: list[str]
    ) -> list[BundleInventoryRow]:
        from src.core.database.repositories.gam_sync import GAMSyncRepository

        rows = GAMSyncRepository(session, tenant_id).list_inventory_by_ids(entity_type, ids)
        return [self._row_from_gam_inventory(r) for r in rows]

    def list_unbundled(
        self,
        session: Session,
        tenant_id: str,
        bundled_ids_by_type: dict[str, set[str]],
        limit: int,
    ) -> list[BundleInventoryRow]:
        from src.core.database.repositories.gam_sync import GAMSyncRepository

        rows = GAMSyncRepository(session, tenant_id).list_inventory_not_in_set(
            inventory_types=("ad_unit", "placement"),
            bundled_ids_by_type=bundled_ids_by_type,
            limit=limit,
        )
        return [self._row_from_gam_inventory(r) for r in rows]

    def list_top_level_placements(self, session: Session, tenant_id: str, limit: int) -> list[BundleInventoryRow]:
        from src.core.database.repositories.gam_sync import GAMSyncRepository

        rows = GAMSyncRepository(session, tenant_id).list_inventory("placement", limit=limit)
        return [self._row_from_gam_inventory(r) for r in rows]

    def find_inventory_item(
        self, session: Session, tenant_id: str, entity_type: str, external_id: str
    ) -> BundleInventoryRow | None:
        from src.core.database.repositories.gam_sync import GAMSyncRepository

        row = GAMSyncRepository(session, tenant_id).find_inventory_item(entity_type, external_id)
        return self._row_from_gam_inventory(row) if row else None


# ---------------------------------------------------------------------------
# FreeWheel + SpringServe stubs
# ---------------------------------------------------------------------------


class _NullInventoryAdapter:
    """Stub adapter for ad servers whose bundle-inventory surfaces aren't
    wired yet (FreeWheel, SpringServe).

    Returns empty results — the bundle UI degrades to "coverage strip
    hidden, no unbundled rail, no seed suggestions" but keeps adapter
    label + vocab visible so the page header reads correctly.

    When a real adapter lands, replace the stub via ``register_adapter``.
    """

    def __init__(self, *, adapter_id: str, label: str, vocab: dict[str, str], ad_server_aliases: set[str]):
        self.adapter_id = adapter_id
        self.label = label
        self.vocab = vocab
        self.matches_tenant_ad_server = ad_server_aliases

    def has_synced_inventory(self, session: Session, tenant_id: str) -> bool:
        return False

    def count_inventory(self, session: Session, tenant_id: str, entity_type: str) -> int:
        return 0

    def list_inventory_by_ids(
        self, session: Session, tenant_id: str, entity_type: str, ids: list[str]
    ) -> list[BundleInventoryRow]:
        return []

    def list_unbundled(
        self,
        session: Session,
        tenant_id: str,
        bundled_ids_by_type: dict[str, set[str]],
        limit: int,
    ) -> list[BundleInventoryRow]:
        return []

    def list_top_level_placements(self, session: Session, tenant_id: str, limit: int) -> list[BundleInventoryRow]:
        return []

    def find_inventory_item(
        self, session: Session, tenant_id: str, entity_type: str, external_id: str
    ) -> BundleInventoryRow | None:
        return None


# ---------------------------------------------------------------------------
# Module-level registration
# ---------------------------------------------------------------------------

register_adapter(_GAMAdapter())
register_adapter(
    _NullInventoryAdapter(
        adapter_id="freewheel",
        label="FreeWheel",
        # FreeWheel vocabulary: "placement groups" wrap "placements".
        vocab={"primary": "placements", "secondary": "placement groups"},
        ad_server_aliases={"freewheel", "fw"},
    )
)
register_adapter(
    _NullInventoryAdapter(
        adapter_id="springserve",
        label="SpringServe",
        # SpringServe vocabulary: tags + demand tags.
        vocab={"primary": "tags", "secondary": "demand tags"},
        ad_server_aliases={"springserve", "ss"},
    )
)
