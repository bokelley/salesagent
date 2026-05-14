"""Shared adapter sync orchestration (#382 Stage 3).

One place where "run a sync" gets executed — regardless of adapter or
sync kind. Replaces the per-adapter button endpoints that each invented
their own logging + result shape. Writes to the ``sync_jobs`` table so
``/admin/scheduling`` (Stage 4) has a uniform feed.

Flow:
    1. Resolve tenant + adapter via the existing get_adapter helper
       (same path the AdCP buyer-facing calls use, so adapter_config /
       tenant-mappings stay consistent).
    2. Create a SyncJob row with status="running".
    3. Call ``adapter.run_inventory_sync()`` or ``run_reporting_sync()``
       based on the requested ``sync_kind``.
    4. Persist the AdapterSyncResult into the SyncJob (status="completed"
       or "failed", counts + errors stamped into the JSON ``progress``
       field for the UI, ``error_message`` for the failure summary).
    5. Return a :class:`SyncExecutionResult` for the immediate caller
       (admin endpoint, scheduler).

GAM's async inventory sync is NOT routed here yet — its existing
``background_sync_service`` writes SyncJob rows directly and runs on a
threaded pattern that doesn't fit this synchronous orchestration. That
migration is a follow-up; for now the two patterns coexist and write to
the same SyncJob table so the Stage 4 UI sees everything uniformly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.adapters.base import AdapterSyncResult, AdServerAdapter
from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob

logger = logging.getLogger(__name__)


# Supported sync_kind values; the SyncJob.sync_type column is generic
# but we pin the set to make orchestration explicit.
SyncKind = str  # "inventory" | "reporting"
KIND_INVENTORY: SyncKind = "inventory"
KIND_REPORTING: SyncKind = "reporting"


@dataclass
class SyncExecutionResult:
    """Summary returned by :func:`execute_sync` to its caller."""

    sync_id: str
    sync_kind: SyncKind
    succeeded: bool
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def scope_pending(self) -> bool:
        """True when the failure was specifically a Tier-1 scope grant
        gap (e.g. FW reporting still IAM-denied). The admin UI renders
        this state with the "awaiting scope" copy rather than a generic
        failure."""
        return bool(self.metadata.get("scope_pending"))


class AdapterDoesNotSupportSyncKind(RuntimeError):
    """Raised when ``execute_sync`` is called with a sync_kind the
    adapter hasn't declared support for. Distinct from a generic
    failure — caller (admin endpoint, scheduler) returns a 4xx-shaped
    response rather than a 5xx, since the request itself is invalid."""

    def __init__(self, adapter_type: str, sync_kind: SyncKind) -> None:
        self.adapter_type = adapter_type
        self.sync_kind = sync_kind
        super().__init__(
            f"Adapter {adapter_type!r} does not declare supports_{sync_kind}_sync=True. "
            "Either enable the capability + override the method, or stop calling "
            f"execute_sync(sync_kind={sync_kind!r}) for this adapter."
        )


def execute_adapter_sync(
    *,
    tenant_id: str,
    adapter_type: str,
    sync_kind: SyncKind,
    triggered_by: str,
    triggered_by_id: str | None = None,
    run_kwargs: dict[str, Any] | None = None,
) -> SyncExecutionResult | None:
    """Resolve the tenant's adapter, then orchestrate a sync run end-to-end.

    Returns ``None`` when the tenant has no AdapterConfig row matching
    ``adapter_type`` — caller maps that to a 400. Distinguishes
    "tenant isn't configured for this adapter" from "the sync itself
    failed" (which is a :class:`SyncExecutionResult` with
    ``succeeded=False``).

    This is the entry point per-adapter buttons + the scheduler (Stage 5)
    both go through; it owns the AdapterConfig lookup + adapter
    construction so callers don't need to duplicate that boilerplate.
    """
    from src.adapters import get_adapter_class
    from src.core.database.repositories.adapter_config import AdapterConfigRepository

    with get_db_session() as session:
        existing = AdapterConfigRepository(session, tenant_id).find_by_tenant()
        if not existing or existing.adapter_type != adapter_type:
            return None
        config_dict = dict(existing.config_json or {})

    adapter_class = get_adapter_class(adapter_type)

    # Stub principal — sync runs operate at the tenant level, not on
    # behalf of a specific principal. Adapters that need an advertiser
    # context for buyer-facing operations don't use it during sync.
    from src.core.schemas import Principal

    stub_principal = Principal(
        tenant_id=tenant_id,
        principal_id="__sync_orchestrator__",
        name="sync-orchestrator",
        platform_mappings={adapter_type: {"advertiser_id": "0"}},
    )

    adapter = adapter_class(
        config=config_dict,
        principal=stub_principal,
        dry_run=False,
        tenant_id=tenant_id,
    )

    return execute_sync(
        adapter=adapter,
        tenant_id=tenant_id,
        sync_kind=sync_kind,
        triggered_by=triggered_by,
        triggered_by_id=triggered_by_id,
        run_kwargs=run_kwargs,
    )


def execute_sync(
    *,
    adapter: AdServerAdapter,
    tenant_id: str,
    sync_kind: SyncKind,
    triggered_by: str,
    triggered_by_id: str | None = None,
    session: Session | None = None,
    run_kwargs: dict[str, Any] | None = None,
) -> SyncExecutionResult:
    """Run one sync end-to-end and persist a SyncJob row for it.

    Args:
        adapter: A live (non-dry-run) :class:`AdServerAdapter`. Caller
            constructs it via the usual ``get_adapter()`` helper so
            tenant config + principal mapping stay consistent with the
            buyer-facing call path.
        tenant_id: Tenant the sync targets — stamped onto the SyncJob.
        sync_kind: ``"inventory"`` or ``"reporting"`` — picks which
            ``run_*_sync()`` method to call.
        triggered_by: Free-form provenance string for the SyncJob
            row (``"admin_button"``, ``"scheduler"``, ``"manual_api"`` etc).
        triggered_by_id: Optional principal_id / user_id for audit lineage.
        session: Optional existing DB session. When omitted, the function
            opens its own session and commits at the end.

    Raises:
        AdapterDoesNotSupportSyncKind: when the adapter's capabilities
            flag for the requested sync_kind is False. Better to fail
            fast at the boundary than to surface a base-class
            NotImplementedError from inside the orchestration.
    """
    adapter_type = getattr(adapter.__class__, "adapter_name", adapter.__class__.__name__)
    if sync_kind not in (KIND_INVENTORY, KIND_REPORTING):
        raise ValueError(f"sync_kind must be 'inventory' or 'reporting'; got {sync_kind!r}")

    capability_flag = (
        adapter.capabilities.supports_inventory_sync
        if sync_kind == KIND_INVENTORY
        else adapter.capabilities.supports_reporting_sync
    )
    if not capability_flag:
        raise AdapterDoesNotSupportSyncKind(adapter_type=adapter_type, sync_kind=sync_kind)

    own_session = session is None
    db = session or get_db_session().__enter__()  # noqa: SIM115 — we manage close below
    try:
        sync_id = f"sync_{uuid.uuid4().hex[:16]}"
        started_at = datetime.now(UTC)
        job = SyncJob(
            sync_id=sync_id,
            tenant_id=tenant_id,
            adapter_type=adapter_type,
            sync_type=sync_kind,
            status="running",
            started_at=started_at,
            triggered_by=triggered_by,
            triggered_by_id=triggered_by_id,
        )
        db.add(job)
        db.flush()

        kwargs = run_kwargs or {}
        try:
            if sync_kind == KIND_INVENTORY:
                result = adapter.run_inventory_sync(**kwargs)
            else:
                result = adapter.run_reporting_sync(**kwargs)
        except Exception as exc:
            # Hard failure inside the adapter — capture, mark the job
            # failed, re-raise upwards so callers can decide on telemetry.
            logger.exception("Adapter %s %s sync raised unexpectedly for tenant=%s", adapter_type, sync_kind, tenant_id)
            job.status = "failed"
            job.completed_at = datetime.now(UTC)
            job.error_message = f"{type(exc).__name__}: {exc}"
            db.flush()
            if own_session:
                db.commit()
            return SyncExecutionResult(
                sync_id=sync_id,
                sync_kind=sync_kind,
                succeeded=False,
                errors={"adapter": str(exc)},
                started_at=started_at,
                finished_at=job.completed_at,
            )

        return _finalize(job, result, db, own_session, started_at, sync_id)
    finally:
        if own_session:
            db.close()


def _finalize(
    job: SyncJob,
    result: AdapterSyncResult,
    db: Session,
    own_session: bool,
    started_at: datetime,
    sync_id: str,
) -> SyncExecutionResult:
    """Stamp the AdapterSyncResult onto the SyncJob row and return the
    caller-facing SyncExecutionResult."""
    job.completed_at = result.finished_at or datetime.now(UTC)
    job.status = "completed" if result.succeeded else "failed"
    job.progress = {
        "counts": dict(result.counts),
        "errors": dict(result.errors),
        "metadata": dict(result.metadata),
    }
    if not result.succeeded and result.errors:
        # Pick the first error message as the human-readable summary;
        # full per-kind errors live in ``progress`` for the UI.
        first_key = next(iter(result.errors))
        job.error_message = f"{first_key}: {result.errors[first_key]}"
    job.summary = (
        f"{result.sync_kind} sync — total={result.total_count} succeeded={result.succeeded} errors={len(result.errors)}"
    )
    db.flush()
    if own_session:
        db.commit()

    return SyncExecutionResult(
        sync_id=sync_id,
        sync_kind=result.sync_kind,
        succeeded=result.succeeded,
        counts=dict(result.counts),
        errors=dict(result.errors),
        metadata=dict(result.metadata),
        started_at=started_at,
        finished_at=job.completed_at,
    )
