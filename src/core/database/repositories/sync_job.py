"""SyncJob repository — tenant-scoped reads of the sync history.

Sprint 3 of [embedded-mode](../../../../docs/design/embedded-mode-sprint-3.md):
``GET /tenants/{tid}/sync-history`` reads from ``sync_jobs``. Existing sync
infrastructure (provision + ``/refresh``) writes rows directly via
``session.add(SyncJob(...))`` because that path is performance-critical and
predates the repository layer; this repository covers the read drill-downs
the management API needs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.core.database.models import SyncJob


class SyncJobRepository:
    """Tenant-scoped reads against the ``sync_jobs`` table.

    Args:
        session: Active SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def list_history(
        self,
        *,
        sync_type: str | None = None,
        status: str | None = None,
        cursor_started_at: datetime | None = None,
        cursor_id: str | None = None,
        limit: int = 20,
    ) -> list[SyncJob]:
        """List sync runs for the tenant, ordered by ``started_at desc, sync_id desc``.

        Cursor pagination uses ``(started_at, sync_id)`` so concurrent inserts
        with the same timestamp can't skip or duplicate rows.
        """
        stmt = select(SyncJob).where(SyncJob.tenant_id == self._tenant_id)

        if sync_type:
            stmt = stmt.where(SyncJob.sync_type == sync_type)
        if status:
            stmt = stmt.where(SyncJob.status == status)

        if cursor_started_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    SyncJob.started_at < cursor_started_at,
                    and_(
                        SyncJob.started_at == cursor_started_at,
                        SyncJob.sync_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(SyncJob.started_at.desc(), SyncJob.sync_id.desc()).limit(limit)
        return list(self._session.scalars(stmt).all())
