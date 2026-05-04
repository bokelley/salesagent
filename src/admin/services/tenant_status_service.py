"""Tenant operational-status aggregation for ``GET /tenants/{tid}/status``.

Single-purpose: produce :class:`TenantStatusResponse` for the Storefront
homepage tile. Cheap, cached, and tolerant of missing data — fields that
have no source today return zero / ``None`` rather than blocking the
endpoint.

Caching is in-memory + per-tenant with a short TTL — fine for sprint 1.5
single-process deployments. A multi-process cache (Redis) lands when
ops asks for it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.admin.api_schemas.tenant_management import (
    StatusAdapterBlock,
    StatusCreativesBlock,
    StatusMediaBuysBlock,
    StatusPackagesBlock,
    StatusSyncRunBlock,
    StatusSyncsBlock,
    StatusWebhooksBlock,
    StatusWorkflowsBlock,
    TenantStatusResponse,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    Context,
    Creative,
    MediaBuy,
    MediaPackage,
    SyncJob,
    Tenant,
    WorkflowStep,
)


# ---------------------------------------------------------------------------
# In-memory cache (5-second TTL — see sprint 1.5 design § Caching)
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 5

_CACHE: dict[str, tuple[float, TenantStatusResponse]] = {}


def invalidate_status_cache(tenant_id: str | None = None) -> None:
    """Drop one tenant's cached status, or the whole cache if no id given.

    Hookable for the invalidation events listed in the sprint 1.5 design
    (adapter test, sync state change, workflow state change, etc.).
    """
    if tenant_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_tenant_status(tenant_id: str) -> TenantStatusResponse | None:
    """Return :class:`TenantStatusResponse` for a tenant, or None if missing.

    Cached: subsequent calls within :data:`CACHE_TTL_SECONDS` return the
    same snapshot. The cache is keyed by tenant_id and busted explicitly
    via :func:`invalidate_status_cache`.
    """
    cached = _CACHE.get(tenant_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            return None
        snapshot = _build_status(session, tenant_id)

    _CACHE[tenant_id] = (now, snapshot)
    return snapshot


def _build_status(session: Session, tenant_id: str) -> TenantStatusResponse:
    return TenantStatusResponse(
        adapter=_adapter_block(session, tenant_id),
        syncs=_syncs_block(session, tenant_id),
        workflows=_workflows_block(session, tenant_id),
        media_buys=_media_buys_block(session, tenant_id),
        packages=_packages_block(session, tenant_id),
        creatives=_creatives_block(session, tenant_id),
        webhooks=_webhooks_block(),
        fetched_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Per-block aggregations
# ---------------------------------------------------------------------------


def _adapter_block(session: Session, tenant_id: str) -> StatusAdapterBlock:
    """Adapter health from the stored config + last connection probe."""
    adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
    if adapter is None:
        return StatusAdapterBlock(type="none", connected=False)

    # The Tenant Management API stores last test results elsewhere when implemented.
    # For now, expose the adapter type and "configured" as a coarse connected signal.
    return StatusAdapterBlock(
        type=adapter.adapter_type,
        connected=True,
        last_tested_at=adapter.updated_at,
        last_test_error=None,
    )


def _syncs_block(session: Session, tenant_id: str) -> StatusSyncsBlock:
    """Sync runs grouped by ``sync_type``."""
    runs = session.scalars(
        select(SyncJob).filter_by(tenant_id=tenant_id).order_by(SyncJob.started_at.desc())
    ).all()

    by_type: dict[str, SyncJob] = {}
    for run in runs:
        # Keep most recent per sync_type only (runs are ordered desc).
        by_type.setdefault(run.sync_type, run)

    return StatusSyncsBlock(
        inventory=_sync_run_block(by_type.get("inventory")),
        custom_targeting=_sync_run_block(by_type.get("custom_targeting")),
        advertisers=_sync_run_block(by_type.get("advertisers")),
    )


def _sync_run_block(run: SyncJob | None) -> StatusSyncRunBlock:
    if run is None:
        return StatusSyncRunBlock()
    status_map = {
        "completed": "success",
        "success": "success",
        "failed": "failed",
        "error": "failed",
        "running": "running",
        "in_progress": "running",
    }
    return StatusSyncRunBlock(
        last_run_at=run.completed_at or run.started_at,
        status=status_map.get(run.status, "never_run"),
        item_count=(run.progress or {}).get("item_count") if run.progress else None,
        error=run.error_message,
    )


def _workflows_block(session: Session, tenant_id: str) -> StatusWorkflowsBlock:
    """Open workflow steps grouped by tool_name (proxy for kind)."""
    open_states = ("pending", "in_progress", "requires_approval")
    steps = session.scalars(
        select(WorkflowStep)
        .join(Context, WorkflowStep.context_id == Context.context_id)
        .where(Context.tenant_id == tenant_id, WorkflowStep.status.in_(open_states))
    ).all()

    if not steps:
        return StatusWorkflowsBlock()

    by_kind: dict[str, int] = {}
    oldest: datetime | None = None
    for step in steps:
        kind = step.tool_name or step.step_type or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if step.created_at is not None and (oldest is None or step.created_at < oldest):
            oldest = step.created_at

    return StatusWorkflowsBlock(
        open_count=len(steps),
        oldest_opened_at=oldest,
        by_kind=by_kind,
    )


def _media_buys_block(session: Session, tenant_id: str) -> StatusMediaBuysBlock:
    active = session.scalar(
        select(func.count())
        .select_from(MediaBuy)
        .where(MediaBuy.tenant_id == tenant_id, MediaBuy.status.in_(("active", "live", "running")))
    )
    pending = session.scalar(
        select(func.count())
        .select_from(MediaBuy)
        .where(
            MediaBuy.tenant_id == tenant_id,
            MediaBuy.status.in_(("pending_approval", "pending", "submitted")),
        )
    )
    return StatusMediaBuysBlock(
        active_count=int(active or 0),
        pending_approval_count=int(pending or 0),
    )


def _packages_block(session: Session, tenant_id: str) -> StatusPackagesBlock:
    """Package counters joined to the parent media buy for tenant scoping.

    ``last_24h_impressions`` is 0 until delivery aggregation is wired in
    (sprint-1.5 known gap — see design Open Q #3).
    """
    active = session.scalar(
        select(func.count())
        .select_from(MediaPackage)
        .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
        .where(MediaBuy.tenant_id == tenant_id, MediaBuy.status.in_(("active", "live", "running")))
    )
    paused = session.scalar(
        select(func.count())
        .select_from(MediaPackage)
        .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
        .where(MediaBuy.tenant_id == tenant_id, MediaBuy.status == "paused")
    )
    return StatusPackagesBlock(
        active_count=int(active or 0),
        paused_count=int(paused or 0),
        last_24h_impressions=0,
    )


def _creatives_block(session: Session, tenant_id: str) -> StatusCreativesBlock:
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    active = session.scalar(
        select(func.count())
        .select_from(Creative)
        .where(Creative.tenant_id == tenant_id, Creative.status.in_(("approved", "active")))
    )
    pending = session.scalar(
        select(func.count())
        .select_from(Creative)
        .where(Creative.tenant_id == tenant_id, Creative.status.in_(("pending", "pending_review")))
    )
    rejected_24h = session.scalar(
        select(func.count())
        .select_from(Creative)
        .where(
            Creative.tenant_id == tenant_id,
            Creative.status == "rejected",
            Creative.updated_at >= twenty_four_hours_ago,
        )
    )
    return StatusCreativesBlock(
        active_count=int(active or 0),
        pending_review_count=int(pending or 0),
        rejected_last_24h_count=int(rejected_24h or 0),
    )


def _webhooks_block() -> StatusWebhooksBlock | None:
    """Returns ``None`` — outbound-webhook delivery aggregation lands in sprint 6."""
    return None
