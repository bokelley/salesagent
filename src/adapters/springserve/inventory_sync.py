"""SpringServe inventory taxonomy sync.

Walks the operator's SpringServe supply hierarchy and KV catalog into the
``springserve_inventory`` cache. The cache feeds the product configuration
UI and the bundle-composition logic that runs at ``get_products`` time --
operators pick from synced inventory, and the composer can intersect a
buyer brief against rich metadata without round-tripping to SpringServe.

What gets cached (one ``entity_type`` per row, explicit FK columns linking
hierarchy):

* ``supply_partner`` -- top-level seller relationships
* ``supply_router`` -- routing groups under a partner; this is the natural
  bundle root because it's where the publisher does the curation work
  (named offering, environment, format, supply scope)
* ``supply_tag`` -- inventory atoms; each tag belongs to a partner directly
  and OPTIONALLY to one router (orphan tags allowed)
* ``key`` -- the publisher's KV namespace catalog (targeting + audience
  surface declared per the publisher's data model)
* ``value_list`` -- named value sets attached to a key; this is where
  publisher-curated audience segments live (e.g. "Audio M25-54" =
  station_id IN <curated list of station IDs>)

When scope isn't granted yet the underlying call surfaces a
:class:`SpringServeForbiddenError`; we trap it once at the top of
:meth:`run` and raise :class:`SupplyScopeNotGranted` so the shared
scheduler gets a clean signal rather than a raw 403.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.adapters.springserve._supply import SpringServeSupplyClient
from src.adapters.springserve._transport import SpringServeForbiddenError
from src.adapters.springserve.client import SpringServeClient, SpringServeError
from src.core.database.repositories.springserve_inventory import (
    SpringServeInventoryRepository,
)

logger = logging.getLogger(__name__)


class SupplyScopeNotGranted(RuntimeError):
    """Raised when supply-side reads return 403.

    See ``docs/adapters/springserve/README.md`` -- the scope ask is
    bundled with the Stage 2/3 write-scope grant request.
    """

    def __init__(self) -> None:
        super().__init__(
            "SpringServe supply-side read scope not granted on this account. "
            "GET /supply_partners and /supply_tags return 403; ask SpringServe "
            "support to enable supply-side read access on the API user."
        )


@dataclass
class InventorySyncResult:
    """Summary of one inventory-sync run."""

    started_at: datetime
    finished_at: datetime
    succeeded: bool
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    rows_updated: int = 0  # alias for ``_wrap_sync_run`` plumbing
    error: str | None = None  # alias for ``_wrap_sync_run`` plumbing


class SpringServeInventorySync:
    """Inventory-taxonomy sync orchestrator."""

    PER_PAGE = 100

    def __init__(self, *, client: SpringServeClient, tenant_id: str, session: Session):
        self._supply = SpringServeSupplyClient(client._transport)
        self._tenant_id = tenant_id
        self._session = session

    def run(self) -> InventorySyncResult:
        """Walk the full supply hierarchy + KV catalog and upsert into the cache.

        Returns an :class:`InventorySyncResult` with per-entity counts.
        Raises :class:`SupplyScopeNotGranted` when the first read hits 403.

        Steps:

        1. List partners, routers, tags (the three supply tiers).
        2. For each router, fetch its tag list and stash the mapping
           so we can backfill ``supply_router_id`` on each tag row --
           neither side carries the back-reference natively.
        3. List keys + value_lists (the KV/audience catalog).
        4. Bulk-upsert everything in one transaction.
        """
        started = datetime.now(UTC)
        counts: dict[str, int] = {}
        try:
            partners = self._fetch_all(self._supply.list_supply_partners)
            routers = self._fetch_all(self._supply.list_supply_routers)
            tags = self._fetch_all(self._supply.list_supply_tags)
            tag_to_router = self._build_tag_to_router_map(routers)
            keys = self._fetch_all(self._supply.list_keys)
            value_lists = self._fetch_all(self._supply.list_value_lists)
        except SpringServeForbiddenError as exc:
            logger.info("SpringServe supply scope not granted: %s", exc)
            raise SupplyScopeNotGranted() from exc
        except SpringServeError as exc:
            logger.warning("SpringServe supply read failed: %s", exc)
            return InventorySyncResult(
                started_at=started,
                finished_at=datetime.now(UTC),
                succeeded=False,
                errors={"supply_read": str(exc)},
                error=str(exc),
            )

        repo = SpringServeInventoryRepository(self._session, self._tenant_id)
        counts["supply_partner"] = repo.bulk_upsert(
            [self._supply_partner_row(p) for p in partners if p.get("id") is not None]
        )
        counts["supply_router"] = repo.bulk_upsert(
            [self._supply_router_row(r) for r in routers if r.get("id") is not None]
        )
        counts["supply_tag"] = repo.bulk_upsert(
            [self._supply_tag_row(t, tag_to_router) for t in tags if t.get("id") is not None]
        )
        counts["key"] = repo.bulk_upsert([self._key_row(k) for k in keys if k.get("id") is not None])
        counts["value_list"] = repo.bulk_upsert(
            [self._value_list_row(v) for v in value_lists if v.get("id") is not None]
        )
        self._session.commit()

        total = sum(counts.values())
        logger.info(
            "SpringServe inventory sync: tenant=%s %s",
            self._tenant_id,
            " ".join(f"{k}={v}" for k, v in counts.items()),
        )
        return InventorySyncResult(
            started_at=started,
            finished_at=datetime.now(UTC),
            succeeded=True,
            counts=counts,
            rows_updated=total,
        )

    # ----- helpers -----

    def _build_tag_to_router_map(self, routers: list[dict[str, Any]]) -> dict[str, str]:
        """For each router, call the filter endpoint and build a tag_id->router_id index.

        SpringServe's API doesn't carry the back-reference on either side
        of the tag<->router relationship, so the only way to discover it is
        to filter the tag list by each router_id. O(routers) extra calls,
        bounded and small (typical publisher has under 20 routers).
        """
        mapping: dict[str, str] = {}
        for router in routers:
            rid = router.get("id")
            if rid is None:
                continue
            page = 1
            while True:
                batch = self._supply.list_supply_tags_in_router(rid, page=page, per_page=self.PER_PAGE)
                if not batch:
                    break
                for tag in batch:
                    tag_id = tag.get("id")
                    if tag_id is not None:
                        # First-seen wins. Live data shows no router-router
                        # overlap, so this is safe; if SpringServe changes
                        # that, the warning surfaces it.
                        tag_key = str(tag_id)
                        if tag_key in mapping and mapping[tag_key] != str(rid):
                            logger.warning(
                                "SpringServe tag %s appears in multiple routers (%s, %s); keeping first",
                                tag_key,
                                mapping[tag_key],
                                rid,
                            )
                        else:
                            mapping[tag_key] = str(rid)
                if len(batch) < self.PER_PAGE:
                    break
                page += 1
        return mapping

    def _fetch_all(self, list_callable: Any) -> list[dict[str, Any]]:
        """Walk paginated results until an empty or short page comes back."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = list_callable(page=page, per_page=self.PER_PAGE)
            if not batch:
                break
            items.extend(batch)
            if len(batch) < self.PER_PAGE:
                break
            page += 1
        return items

    # ----- row mappers -----

    @staticmethod
    def _supply_partner_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "supply_partner",
            "entity_id": str(row["id"]),
            "name": row.get("name"),
            "supply_partner_id": None,
            "supply_router_id": None,
            "key_id": None,
            "raw_json": row,
        }

    @staticmethod
    def _supply_router_row(row: dict[str, Any]) -> dict[str, Any]:
        partner_id = row.get("supply_partner_id")
        return {
            "entity_type": "supply_router",
            "entity_id": str(row["id"]),
            "name": row.get("name"),
            "supply_partner_id": str(partner_id) if partner_id is not None else None,
            "supply_router_id": None,
            "key_id": None,
            "raw_json": row,
        }

    @staticmethod
    def _supply_tag_row(row: dict[str, Any], tag_to_router: dict[str, str]) -> dict[str, Any]:
        partner_id = row.get("supply_partner_id")
        router_id = tag_to_router.get(str(row["id"]))
        return {
            "entity_type": "supply_tag",
            "entity_id": str(row["id"]),
            "name": row.get("name"),
            "supply_partner_id": str(partner_id) if partner_id is not None else None,
            "supply_router_id": router_id,
            "key_id": None,
            "raw_json": row,
        }

    @staticmethod
    def _key_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "key",
            "entity_id": str(row["id"]),
            "name": row.get("name") or row.get("key"),
            "supply_partner_id": None,
            "supply_router_id": None,
            "key_id": None,
            "raw_json": row,
        }

    @staticmethod
    def _value_list_row(row: dict[str, Any]) -> dict[str, Any]:
        key_id = row.get("key_id")
        return {
            "entity_type": "value_list",
            "entity_id": str(row["id"]),
            "name": row.get("name"),
            "supply_partner_id": None,
            "supply_router_id": None,
            "key_id": str(key_id) if key_id is not None else None,
            "raw_json": row,
        }
