"""SpringServe inventory taxonomy sync.

Walks the operator's SpringServe supply-side entities and upserts them
into the ``springserve_inventory`` cache. The cache feeds the product
configuration UI -- operators pick from synced supply tags instead of
typing IDs.

Today: the supply-side GETs (``/supply_partners``, ``/supply_tags``)
return 403 on the operator's account; the sync raises
:class:`SupplyScopeNotGranted` so the scheduler logs and retries
without exception spam. The Stage 5 README documents the scope ask.

Day-of-scope: verify response shapes match the assumed
``{id, name, supply_partner_id}`` minimum and tune
:meth:`_supply_tag_row_to_dict` if the field names differ.
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
        """Walk supply_partners + supply_tags and upsert into the cache.

        Returns an :class:`InventorySyncResult` with per-entity counts.
        Raises :class:`SupplyScopeNotGranted` when the first read hits 403.
        """
        started = datetime.now(UTC)
        counts: dict[str, int] = {}
        errors: dict[str, str] = {}
        try:
            partners = self._fetch_all(self._supply.list_supply_partners)
            tags = self._fetch_all(self._supply.list_supply_tags)
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
        touched_partners = repo.bulk_upsert(
            [self._supply_partner_row_to_dict(p) for p in partners if p.get("id") is not None]
        )
        touched_tags = repo.bulk_upsert([self._supply_tag_row_to_dict(t) for t in tags if t.get("id") is not None])
        self._session.commit()
        counts["supply_partner"] = touched_partners
        counts["supply_tag"] = touched_tags
        logger.info(
            "SpringServe inventory sync: tenant=%s partners=%d tags=%d",
            self._tenant_id,
            touched_partners,
            touched_tags,
        )
        return InventorySyncResult(
            started_at=started,
            finished_at=datetime.now(UTC),
            succeeded=True,
            counts=counts,
            errors=errors,
            rows_updated=touched_partners + touched_tags,
        )

    def _fetch_all(self, list_callable: Any) -> list[dict[str, Any]]:
        """Walk paginated results until an empty page comes back."""
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

    @staticmethod
    def _supply_partner_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "supply_partner",
            "entity_id": str(row["id"]),
            "name": row.get("name"),
            "parent_id": None,
            "raw_json": row,
        }

    @staticmethod
    def _supply_tag_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "supply_tag",
            "entity_id": str(row["id"]),
            "name": row.get("name"),
            "parent_id": str(row["supply_partner_id"]) if row.get("supply_partner_id") is not None else None,
            "raw_json": row,
        }
