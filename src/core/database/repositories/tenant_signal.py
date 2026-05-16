"""TenantSignal repository — tenant-scoped data access.

Operator-authored map of one adapter targeting capability. Mirrors AdCP's
``Signal`` shape (value_type, categories, range) so storefronts can render
UI for any signal type without per-adapter branching. Adapter-specific
resolution lives in ``adapter_config`` (opaque to storefront, consumed by
the per-adapter materializer).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import TenantSignal


class TenantSignalRepository:
    """Tenant-scoped data access for TenantSignal."""

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "tenant_id", "signal_id", "created_at"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_by_id(self, signal_id: str) -> TenantSignal | None:
        return self._session.scalars(
            select(TenantSignal).where(
                TenantSignal.tenant_id == self._tenant_id,
                TenantSignal.signal_id == signal_id,
            )
        ).first()

    def list_by_ids(self, signal_ids: list[str]) -> list[TenantSignal]:
        if not signal_ids:
            return []
        stmt = select(TenantSignal).where(
            TenantSignal.tenant_id == self._tenant_id,
            TenantSignal.signal_id.in_(signal_ids),
        )
        return list(self._session.scalars(stmt).all())

    def list_all(self, updated_since: datetime | None = None) -> list[TenantSignal]:
        stmt = select(TenantSignal).where(TenantSignal.tenant_id == self._tenant_id)
        if updated_since is not None:
            stmt = stmt.where(TenantSignal.updated_at > updated_since)
        return list(self._session.scalars(stmt.order_by(TenantSignal.signal_id)).all())

    def add(self, signal: TenantSignal) -> None:
        if signal.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: signal.tenant_id={signal.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(signal)

    def delete(self, signal: TenantSignal) -> None:
        if signal.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: signal.tenant_id={signal.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.delete(signal)
