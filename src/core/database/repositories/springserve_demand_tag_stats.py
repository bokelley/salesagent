"""Repository for the SpringServe demand-tag-stats cache.

Centralises tenant-scoped reads and bulk upserts of
``springserve_demand_tag_stats`` rows. Read paths feed
``SpringServeAdapter.get_packages_snapshot`` and
``SpringServeAdapter.get_media_buy_delivery``. Write paths feed the
Reporting API sync job (Stage 4).

Core invariant: every query filters by ``tenant_id``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database.models import SpringServeDemandTagStats


class SpringServeDemandTagStatsRepository:
    """Tenant-scoped access for the SpringServe demand-tag-stats cache."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_by_demand_tag_ids(self, demand_tag_ids: Iterable[str]) -> dict[str, SpringServeDemandTagStats]:
        """Return stats rows keyed by demand_tag_id. Missing IDs are omitted
        (callers handle the absence as 'no data yet')."""
        ids = list(demand_tag_ids)
        if not ids:
            return {}
        stmt = select(SpringServeDemandTagStats).filter(
            SpringServeDemandTagStats.tenant_id == self._tenant_id,
            SpringServeDemandTagStats.demand_tag_id.in_(ids),
        )
        return {row.demand_tag_id: row for row in self._session.scalars(stmt).all()}

    def list_by_campaign(self, campaign_id: str) -> list[SpringServeDemandTagStats]:
        """Return all cached demand-tag stats for one campaign. Used by
        ``get_media_buy_delivery`` to aggregate totals across packages."""
        stmt = select(SpringServeDemandTagStats).filter_by(tenant_id=self._tenant_id, campaign_id=campaign_id)
        return list(self._session.scalars(stmt).all())

    def bulk_upsert(self, rows: Iterable[dict]) -> int:
        """Insert or update demand-tag-stats rows. ``rows`` items must carry
        ``demand_tag_id``, ``impressions``, ``spend_micros``, ``as_of`` at
        minimum; ``tenant_id`` is forced to the repository's scope.

        Returns the number of rows touched.
        """
        payloads = [{**row, "tenant_id": self._tenant_id} for row in rows]
        if not payloads:
            return 0
        stmt = pg_insert(SpringServeDemandTagStats).values(payloads)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in SpringServeDemandTagStats.__table__.columns
            if col.name not in ("tenant_id", "demand_tag_id")
        }
        stmt = stmt.on_conflict_do_update(index_elements=["tenant_id", "demand_tag_id"], set_=update_cols)
        result = self._session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0

    def latest_sync_at(self) -> datetime | None:
        """Return the most recent ``last_synced_at`` for this tenant, or
        ``None`` if the reporting sync has never run. The freshness
        banner uses this to flag stale or never-run reporting."""
        stmt = select(func.max(SpringServeDemandTagStats.last_synced_at)).filter_by(tenant_id=self._tenant_id)
        return self._session.scalar(stmt)
