"""Repository for the FreeWheel placement-stats cache.

Centralises tenant-scoped reads and bulk upserts of
``freewheel_placement_stats`` rows. Read paths feed
``FreeWheelAdapter.get_packages_snapshot`` and
``FreeWheelAdapter.get_media_buy_delivery``. Write paths feed the
(forthcoming) Query Reporting API sync job.

Core invariant: every query filters by ``tenant_id``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database.models import FreeWheelPlacementStats


class FreeWheelPlacementStatsRepository:
    """Tenant-scoped access for the FW placement-stats cache."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_by_placement_ids(self, placement_ids: Iterable[str]) -> dict[str, FreeWheelPlacementStats]:
        """Return stats rows keyed by placement_id. Missing placements are
        omitted (callers handle the absence as 'no data yet')."""
        ids = list(placement_ids)
        if not ids:
            return {}
        stmt = select(FreeWheelPlacementStats).filter(
            FreeWheelPlacementStats.tenant_id == self._tenant_id,
            FreeWheelPlacementStats.placement_id.in_(ids),
        )
        return {row.placement_id: row for row in self._session.scalars(stmt).all()}

    def list_by_insertion_order(self, insertion_order_id: str) -> list[FreeWheelPlacementStats]:
        """Return all cached placement stats for one IO. Used by
        ``get_media_buy_delivery`` to aggregate totals across packages."""
        stmt = select(FreeWheelPlacementStats).filter_by(
            tenant_id=self._tenant_id, insertion_order_id=insertion_order_id
        )
        return list(self._session.scalars(stmt).all())

    def bulk_upsert(self, rows: Iterable[dict]) -> int:
        """Insert or update placement-stats rows. ``rows`` items must carry
        ``placement_id``, ``impressions``, ``spend_micros``, ``as_of`` at
        minimum; ``tenant_id`` is forced to the repository's scope.

        Returns the number of rows touched. Used by the reporting sync job
        once Tier 2 FW scope is granted.
        """
        payloads = [{**row, "tenant_id": self._tenant_id} for row in rows]
        if not payloads:
            return 0
        stmt = pg_insert(FreeWheelPlacementStats).values(payloads)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in FreeWheelPlacementStats.__table__.columns
            if col.name not in ("tenant_id", "placement_id")
        }
        stmt = stmt.on_conflict_do_update(index_elements=["tenant_id", "placement_id"], set_=update_cols)
        result = self._session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0

    def latest_sync_at(self) -> datetime | None:
        """Return the most recent ``last_synced_at`` across all cached placement
        stats for this tenant, or ``None`` if the reporting sync has never run.
        The freshness banner uses this to flag stale or never-run reporting
        — buyer-facing pacing will be wrong if this drifts too far behind."""
        stmt = select(func.max(FreeWheelPlacementStats.last_synced_at)).filter_by(tenant_id=self._tenant_id)
        return self._session.scalar(stmt)
