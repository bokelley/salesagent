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
    SetupTaskItem,
    SetupTasksBlock,
    StatusAdapterBlock,
    StatusCreativesBlock,
    StatusMediaBuysBlock,
    StatusPackagesBlock,
    StatusProductsBlock,
    StatusSyncRunBlock,
    StatusSyncsBlock,
    StatusWebhooksBlock,
    StatusWorkflowsBlock,
    TenantStatusResponse,
)
from src.admin.utils.embedded_capabilities import publisher_owns
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    Context,
    Creative,
    MediaBuy,
    MediaPackage,
    Product,
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
        products=_products_block(session, tenant_id),
        creatives=_creatives_block(session, tenant_id),
        webhooks=_webhooks_block(),
        setup_tasks=_setup_tasks_block(session, tenant_id),
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
    runs = session.scalars(select(SyncJob).filter_by(tenant_id=tenant_id).order_by(SyncJob.started_at.desc())).all()

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
    """Package counters joined to the parent media buy for tenant scoping."""
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
    )


def _products_block(session: Session, tenant_id: str) -> StatusProductsBlock:
    """Product counters split by archived state.

    Sprint 1.8 follow-up — Storefront's homepage uses ``active_count``
    as the primary "what's the publisher selling?" signal. Distinct
    from packages because one product fans out to N priced packages.

    The Product model doesn't carry an explicit status field today;
    ``archived_at IS NULL`` rows count active, non-null rows count
    archived. ``draft_count`` always 0 — the field is reserved for
    when a draft state lands so Storefront can light up a "Drafts"
    badge without an API shape change.
    """
    active = session.scalar(
        select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id, Product.archived_at.is_(None))
    )
    archived = session.scalar(
        select(func.count())
        .select_from(Product)
        .where(Product.tenant_id == tenant_id, Product.archived_at.is_not(None))
    )
    return StatusProductsBlock(
        active_count=int(active or 0),
        draft_count=0,
        archived_count=int(archived or 0),
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


# ---------------------------------------------------------------------------
# Sprint 1.8 §7 — setup_tasks block (folds setup_checklist into /status)
# ---------------------------------------------------------------------------


# Per the design table:
#   public_agent_url → platform on managed, publisher on open-instance
#   sso_configuration → hidden on managed (multi-tenant runtime already gates it
#     out), publisher on open-instance
#   authorized_properties (legacy) → hidden everywhere (deprecated)
#   everything else → publisher, unless EMBEDDED_CAPABILITIES has handed the
#     underlying workflow to the storefront (see _TASK_CAPABILITY below)
_PLATFORM_KEYS_WHEN_MANAGED = frozenset(("public_agent_url",))
_HIDDEN_KEYS = frozenset(("authorized_properties",))

# Setup-task keys whose completion is gated on a storefront-ownable capability.
# When the storefront owns the capability (EMBEDDED_CAPABILITIES[<cap>] ==
# "storefront"), the publisher can't fix it — the corresponding UI section is
# hidden by the {% if publisher_owns(...) %} gate, so surfacing the item with
# a publisher scope produces an action the seller can't act on. Treating it as
# platform scope routes it the same way as public_agent_url: visible to hosts
# via the Tenant Management API, suppressed in the publisher-facing /status.
#
# Mirror this map when adding a new setup task in SetupChecklistService whose
# admin UI is gated by `publisher_owns(<cap>)` in a template. Without an entry
# here the task surfaces to embedded sellers as a publisher action they can't
# act on. Capability strings must match the values used in the Jinja gates and
# in EMBEDDED_CAPABILITIES — see src/admin/utils/embedded_capabilities.py.
_TASK_CAPABILITY: dict[str, str] = {
    "slack_integration": "slack",
    "gemini_api_key": "ai_services",
    "creative_approval_guidelines": "creative_approval",
    "signals_agent": "signals_agents",
    "products_created": "compose_products",
}


# Configure paths are relative to the tenant root so Storefront can compose
# them against whatever iframe prefix it chooses.
_CONFIGURE_PATHS: dict[str, str] = {
    # public_agent_url is derived from Custom Domain on the Account screen,
    # so we send users there rather than the Publishers section (where the
    # URL is shown but not editable).
    "public_agent_url": "/settings#account",
    "default_gam_advertiser_id": "/settings#advertiser-routing",
    "ad_server_connected": "/settings#adserver",
    "currency_limits": "/settings#business-rules",
    "sso_configuration": "/users",
    "products_created": "/products",
    "principals_created": "/settings#advertisers",
    "inventory_synced": "/inventory",
}


def _scope_for_task(key: str, *, is_managed: bool) -> str | None:
    """Return ``platform``/``publisher`` for a task key, or None to hide."""
    if key in _HIDDEN_KEYS:
        return None
    if key in _PLATFORM_KEYS_WHEN_MANAGED:
        return "platform" if is_managed else "publisher"
    if is_managed and key in _TASK_CAPABILITY and not publisher_owns(_TASK_CAPABILITY[key]):
        return "platform"
    return "publisher"


def _severity_for_task(*, tier: str, is_complete: bool) -> str:
    """Map (tier, complete) → severity per sprint 1.8 §7."""
    if is_complete:
        return "info"
    if tier == "critical":
        return "blocker"
    if tier == "recommended":
        return "warning"
    return "info"


def _setup_tasks_block(session: Session, tenant_id: str) -> SetupTasksBlock:
    """Project the setup checklist onto the /status response shape.

    Reads the existing :class:`SetupChecklistService` output, annotates
    each task with severity + scope, drops hidden tasks, and returns
    the rolled-up :class:`SetupTasksBlock`.
    """
    # Local import keeps this service independent of the checklist
    # module's transitive imports (which include its own session bootstrap).
    from src.services.setup_checklist_service import SetupChecklistService

    tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
    is_managed = bool(tenant and tenant.is_embedded)

    checklist = SetupChecklistService(tenant_id).get_setup_status()

    items: list[SetupTaskItem] = []
    blocker_count = 0
    warning_count = 0

    for tier in ("critical", "recommended", "optional"):
        for task in checklist.get(tier, []):
            key = task["key"]
            scope = _scope_for_task(key, is_managed=is_managed)
            if scope is None:
                continue
            # Embedded tenants: suppress platform-scope items entirely.
            # The host (Scope3 / etc.) already knows its own provisioning
            # state via the management API; surfacing platform items in
            # the publisher-facing /status response just creates noise.
            # If a host needs visibility into platform gaps, use the
            # Tenant Management API directly — that's the system-of-record.
            if is_managed and scope == "platform":
                continue
            severity = _severity_for_task(tier=tier, is_complete=task["is_complete"])
            if severity == "blocker":
                blocker_count += 1
            elif severity == "warning":
                warning_count += 1

            items.append(
                SetupTaskItem(
                    id=key,
                    name=task["name"],
                    severity=severity,
                    scope=scope,
                    description=task["description"],
                    is_complete=task["is_complete"],
                    configure_path=_CONFIGURE_PATHS.get(key),
                )
            )

    return SetupTasksBlock(
        blocker_count=blocker_count,
        warning_count=warning_count,
        items=items,
    )
