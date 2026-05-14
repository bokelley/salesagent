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
import re
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

# Max length for ``SyncJob.error_message``. The column is TEXT so the DB
# doesn't truncate, but the field renders cross-tenant on the super-admin
# scheduling page — bounding it both prevents pathological full-traceback
# strings from breaking the UI AND limits the size of any accidental
# credential bleed (#382 security review).
_MAX_ERROR_MESSAGE_LEN = 500


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

    def to_json_payload(self) -> dict[str, Any]:
        """Canonical JSON body for HTTP endpoints that dispatch a sync.

        Shared between the per-adapter buttons (e.g. FW's
        ``sync-inventory``/``sync-reporting`` endpoints) and the
        cross-tenant ``/admin/api/scheduling/run`` so the shape stays
        consistent as new adapter buttons get added (and the buyer-facing
        JS doesn't need adapter-specific branches).
        """
        return {
            "sync_id": self.sync_id,
            "sync_kind": self.sync_kind,
            "succeeded": self.succeeded,
            "counts": dict(self.counts),
            "errors": dict(self.errors),
            "metadata": dict(self.metadata),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "scope_pending": self.scope_pending,
        }


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
    sync_id: str | None = None,
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
        sync_id=sync_id,
    )


class _EnqueueValidationError(RuntimeError):
    """Raised by :func:`enqueue_adapter_sync` when the synchronous
    pre-checks fail (tenant unconfigured, capability missing). Distinct
    type so the Flask wrapper can map ``None``/exception to the right
    HTTP status without leaking the SyncJob.queued row."""

    def __init__(self, http_status: int, message: str) -> None:
        self.http_status = http_status
        super().__init__(message)


def enqueue_adapter_sync(
    *,
    tenant_id: str,
    adapter_type: str,
    sync_kind: SyncKind,
    triggered_by: str,
    triggered_by_id: str | None = None,
    run_kwargs: dict[str, Any] | None = None,
) -> str | None:
    """Validate + enqueue a sync run, return the new ``sync_id`` immediately.

    Splits the cheap synchronous parts from the expensive adapter call:
      1. Resolve AdapterConfig + check capability (fast — DB lookup).
      2. Create a SyncJob row with ``status='queued'``.
      3. Spawn a daemon thread that runs the actual sync via
         :func:`execute_adapter_sync` with the pre-generated ``sync_id``;
         the orchestrator transitions ``queued`` → ``running`` → terminal.
      4. Return the ``sync_id`` so the HTTP caller can respond with 202
         and the UI can poll for status.

    Returns ``None`` if the tenant has no AdapterConfig matching
    ``adapter_type`` (caller maps to 400). Raises
    :class:`AdapterDoesNotSupportSyncKind` if the capability is off.

    Used by the cross-tenant ``/admin/api/scheduling/run`` endpoint so a
    long-running GAM inventory sync doesn't hold a Flask request thread
    open past nginx's idle timeout.
    """
    import threading

    from src.adapters import get_adapter_class
    from src.core.database.repositories.adapter_config import AdapterConfigRepository

    if sync_kind not in (KIND_INVENTORY, KIND_REPORTING):
        raise ValueError(f"sync_kind must be 'inventory' or 'reporting'; got {sync_kind!r}")

    with get_db_session() as session:
        cfg = AdapterConfigRepository(session, tenant_id).find_by_tenant()
        if not cfg or cfg.adapter_type != adapter_type:
            return None

    adapter_class = get_adapter_class(adapter_type)
    caps = getattr(adapter_class, "capabilities", None)
    if caps is None:
        raise AdapterDoesNotSupportSyncKind(adapter_type=adapter_type, sync_kind=sync_kind)
    flag = caps.supports_inventory_sync if sync_kind == KIND_INVENTORY else caps.supports_reporting_sync
    if not flag:
        raise AdapterDoesNotSupportSyncKind(adapter_type=adapter_type, sync_kind=sync_kind)

    sync_id = f"sync_{uuid.uuid4().hex[:16]}"
    with get_db_session() as session:
        session.add(
            SyncJob(
                sync_id=sync_id,
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                sync_type=sync_kind,
                status="queued",
                started_at=datetime.now(UTC),
                triggered_by=triggered_by,
                triggered_by_id=triggered_by_id,
            )
        )
        session.commit()

    def _runner() -> None:
        try:
            execute_adapter_sync(
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                sync_kind=sync_kind,
                triggered_by=triggered_by,
                triggered_by_id=triggered_by_id,
                run_kwargs=run_kwargs,
                sync_id=sync_id,
            )
        except Exception:
            # Mirror the orchestrator's defensive logging — daemon thread
            # exceptions otherwise vanish silently. The SyncJob row will
            # remain ``queued`` if execute_adapter_sync didn't transition
            # it; surface that via a follow-up update so the admin UI
            # doesn't show a stuck row indefinitely.
            logger.exception(
                "enqueue_adapter_sync runner crashed for sync_id=%s tenant=%s adapter=%s",
                sync_id,
                tenant_id,
                adapter_type,
            )
            _mark_runner_crash(sync_id, tenant_id)

    threading.Thread(target=_runner, daemon=True, name=f"sync-{sync_id}").start()
    return sync_id


def _mark_runner_crash(sync_id: str, tenant_id: str) -> None:
    """Best-effort failure stamp when the async runner thread crashes
    before :func:`execute_sync` ran. Lookup the queued row and mark it
    failed so the UI doesn't show a stuck-forever ``queued`` row."""
    from src.core.database.repositories.sync_job import SyncJobRepository

    try:
        with get_db_session() as session:
            job = SyncJobRepository(session, tenant_id).find_by_sync_id(sync_id)
            if job is None:
                return
            if job.status not in ("queued", "running"):
                return
            job.status = "failed"
            job.completed_at = datetime.now(UTC)
            job.error_message = "runner thread crashed before sync started"
            session.commit()
    except Exception:
        logger.exception("Failed to mark crashed runner state for sync_id=%s", sync_id)


def execute_sync(
    *,
    adapter: AdServerAdapter,
    tenant_id: str,
    sync_kind: SyncKind,
    triggered_by: str,
    triggered_by_id: str | None = None,
    session: Session | None = None,
    run_kwargs: dict[str, Any] | None = None,
    sync_id: str | None = None,
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

    if session is not None:
        return _execute_sync_with_session(
            session,
            adapter=adapter,
            adapter_type=adapter_type,
            tenant_id=tenant_id,
            sync_kind=sync_kind,
            triggered_by=triggered_by,
            triggered_by_id=triggered_by_id,
            run_kwargs=run_kwargs,
            own_session=False,
            sync_id=sync_id,
        )

    with get_db_session() as db:
        return _execute_sync_with_session(
            db,
            adapter=adapter,
            adapter_type=adapter_type,
            tenant_id=tenant_id,
            sync_kind=sync_kind,
            triggered_by=triggered_by,
            triggered_by_id=triggered_by_id,
            run_kwargs=run_kwargs,
            own_session=True,
            sync_id=sync_id,
        )


def _execute_sync_with_session(
    db: Session,
    *,
    adapter: AdServerAdapter,
    adapter_type: str,
    tenant_id: str,
    sync_kind: SyncKind,
    triggered_by: str,
    triggered_by_id: str | None,
    run_kwargs: dict[str, Any] | None,
    own_session: bool,
    sync_id: str | None = None,
) -> SyncExecutionResult:
    """Body of :func:`execute_sync`, separated so the caller can decide
    whether to wrap it in a ``with get_db_session()`` block (own_session=True)
    or reuse a caller-supplied session.

    When ``sync_id`` is provided, the function looks for an existing
    SyncJob row with that ID (the ``enqueue_adapter_sync`` async path
    pre-creates one with ``status='queued'`` so it can return the ID
    to the HTTP caller immediately). If the row exists, transition it
    queued → running. Otherwise create a new row with the supplied ID.
    """
    from src.core.database.repositories.sync_job import SyncJobRepository

    if sync_id is None:
        sync_id = f"sync_{uuid.uuid4().hex[:16]}"
    started_at = datetime.now(UTC)

    existing = SyncJobRepository(db, tenant_id).find_by_sync_id(sync_id)
    if existing is not None:
        job = existing
        job.status = "running"
        job.started_at = started_at
    else:
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
        logger.exception("Adapter %s %s sync raised unexpectedly for tenant=%s", adapter_type, sync_kind, tenant_id)
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.error_message = _sanitize_error_message(f"{type(exc).__name__}: {exc}")
        db.flush()
        if own_session:
            db.commit()
        return SyncExecutionResult(
            sync_id=sync_id,
            sync_kind=sync_kind,
            succeeded=False,
            errors={"adapter": _sanitize_error_message(str(exc))},
            started_at=started_at,
            finished_at=job.completed_at,
        )

    return _finalize(job, result, db, own_session, started_at, sync_id)


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
        job.error_message = _sanitize_error_message(f"{first_key}: {result.errors[first_key]}")
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


# Patterns most likely to leak secrets into adapter exception strings.
# Conservative — false positives are fine (visible as ``[redacted]``),
# false negatives leak across the cross-tenant scheduling view.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL),  # PEM blocks
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)(refresh_token|access_token|api_key|password|secret)[\"'=:\s]+[^\s,\"'}]{8,}"),
)


def _sanitize_error_message(msg: str) -> str:
    """Strip likely secrets out of an adapter exception string and bound
    the length before persisting to ``SyncJob.error_message``.

    The row is read by super-admins across all tenants — a stray refresh
    token or service-account JSON in a Python traceback would otherwise
    bleed cross-tenant. We can't promise to catch every secret shape, so
    the length cap is the second line of defense (no full credential
    payload fits in 500 chars after the prefix).
    """
    if not msg:
        return msg
    for pattern in _SECRET_PATTERNS:
        msg = pattern.sub("[redacted]", msg)
    if len(msg) > _MAX_ERROR_MESSAGE_LEN:
        msg = msg[: _MAX_ERROR_MESSAGE_LEN - 1] + "…"
    return msg
