"""Cross-tenant scheduling view (#382 Stage 4).

Assembles the data behind ``/admin/scheduling``: one row per
``(tenant_id, adapter_type, sync_kind)`` where the adapter declares
support for that kind, paired with the most recent SyncJob row (if any)
and a freshness verdict.

Reads only — the Run Now action goes through
``src.services.adapter_sync_orchestration.execute_adapter_sync``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.adapters import ADAPTER_REGISTRY
from src.core.database.models import SyncJob
from src.core.database.repositories.adapter_config import AdapterConfigAdminRepository
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services.adapter_sync_orchestration import KIND_INVENTORY, KIND_REPORTING

# Freshness thresholds. Inventory taxonomies shift slowly (24h);
# reporting feeds the delivery pipeline so 2h is one missed hourly cycle.
# These match the per-adapter thresholds in adapters.py::freewheel_cache_freshness
# so the cross-tenant view doesn't disagree with the per-tenant view.
INVENTORY_STALE_AFTER = timedelta(hours=24)
REPORTING_STALE_AFTER = timedelta(hours=2)

_SYNC_KINDS = (KIND_INVENTORY, KIND_REPORTING)


@dataclass
class SchedulingRow:
    """One row in the scheduling matrix."""

    tenant_id: str
    tenant_name: str
    adapter_type: str
    sync_kind: str
    supported: bool  # adapter.capabilities.supports_<kind>_sync
    last_status: str | None  # "running" | "completed" | "failed" | None
    last_started_at: datetime | None
    last_completed_at: datetime | None
    last_sync_id: str | None
    last_error_message: str | None
    stale: bool  # True when the freshest completed run is older than the kind's threshold
    never_run: bool  # True when no SyncJob row exists at all for this triple

    @property
    def freshness_age_seconds(self) -> int | None:
        if self.last_completed_at is None:
            return None
        # SyncJob.completed_at is timezone-aware (DateTime(timezone=True))
        return int((datetime.now(UTC) - self.last_completed_at).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant_name,
            "adapter_type": self.adapter_type,
            "sync_kind": self.sync_kind,
            "supported": self.supported,
            "last_status": self.last_status,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_completed_at": self.last_completed_at.isoformat() if self.last_completed_at else None,
            "last_sync_id": self.last_sync_id,
            "last_error_message": self.last_error_message,
            "stale": self.stale,
            "never_run": self.never_run,
            "freshness_age_seconds": self.freshness_age_seconds,
        }


def _capability_flag(adapter_type: str, sync_kind: str) -> bool:
    """Return the adapter class's declared support for the given kind.

    Looks at :class:`AdapterCapabilities` directly rather than instantiating
    the adapter — the scheduling page must render for tenants whose
    AdapterConfig is incomplete or whose credentials are missing.
    """
    adapter_class = ADAPTER_REGISTRY.get(adapter_type.lower())
    if adapter_class is None:
        return False
    caps = getattr(adapter_class, "capabilities", None)
    if caps is None:
        return False
    attr = "supports_inventory_sync" if sync_kind == KIND_INVENTORY else "supports_reporting_sync"
    return bool(getattr(caps, attr, False))


def _stale_threshold(sync_kind: str) -> timedelta:
    return INVENTORY_STALE_AFTER if sync_kind == KIND_INVENTORY else REPORTING_STALE_AFTER


def _build_row(
    *,
    tenant_id: str,
    tenant_name: str,
    adapter_type: str,
    sync_kind: str,
    job: SyncJob | None,
    now: datetime,
) -> SchedulingRow:
    supported = _capability_flag(adapter_type, sync_kind)
    if job is None:
        return SchedulingRow(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            adapter_type=adapter_type,
            sync_kind=sync_kind,
            supported=supported,
            last_status=None,
            last_started_at=None,
            last_completed_at=None,
            last_sync_id=None,
            last_error_message=None,
            stale=supported,  # supported but never run → stale (action needed)
            never_run=True,
        )

    threshold = _stale_threshold(sync_kind)
    # Only "completed" runs count toward freshness; a failed run leaves the
    # cache as-old-as-before. ``stale`` mirrors that: if there's no completed
    # run yet, we're stale; otherwise compare to threshold.
    if job.status == "completed" and job.completed_at is not None:
        stale = (now - job.completed_at) > threshold
    else:
        stale = True

    return SchedulingRow(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        adapter_type=adapter_type,
        sync_kind=sync_kind,
        supported=supported,
        last_status=job.status,
        last_started_at=job.started_at,
        last_completed_at=job.completed_at,
        last_sync_id=job.sync_id,
        last_error_message=job.error_message,
        stale=stale,
        never_run=False,
    )


def build_scheduling_matrix(session: Session) -> list[SchedulingRow]:
    """Return the full ``/admin/scheduling`` matrix.

    Strategy:
      1. List every tenant that has an AdapterConfig row — those are the
         tenants the scheduler can act on.
      2. Fan out into ``(inventory, reporting)`` per (tenant, adapter_type).
      3. Skip rows where the adapter doesn't declare the capability —
         showing them would imply runnability that doesn't exist.
      4. Pull the most-recent SyncJob row for the remaining triples in
         one cross-tenant query.

    Returns rows sorted by (tenant_name, adapter_type, sync_kind) so the
    HTML table is stable across requests.
    """
    pairs = AdapterConfigAdminRepository(session).list_all()
    if not pairs:
        return []

    expected_triples: list[tuple[str, str, str]] = [
        (p.tenant_id, p.adapter_type, kind)
        for p in pairs
        for kind in _SYNC_KINDS
        if _capability_flag(p.adapter_type, kind)
    ]

    latest = SyncJobAdminRepository(session).latest_for_triples(expected_triples)

    now = datetime.now(UTC)
    rows: list[SchedulingRow] = []
    for pair in pairs:
        for kind in _SYNC_KINDS:
            if not _capability_flag(pair.adapter_type, kind):
                continue
            rows.append(
                _build_row(
                    tenant_id=pair.tenant_id,
                    tenant_name=pair.tenant_name,
                    adapter_type=pair.adapter_type,
                    sync_kind=kind,
                    job=latest.get((pair.tenant_id, pair.adapter_type, kind)),
                    now=now,
                )
            )

    rows.sort(key=lambda r: (r.tenant_name.lower(), r.adapter_type, r.sync_kind))
    return rows
