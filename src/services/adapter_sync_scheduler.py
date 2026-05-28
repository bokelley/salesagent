"""Server-owned schedulers for adapter sync work.

The reporting scheduler landed first and only knew about ``reporting``.
Inventory and guidance syncs use the same eligibility rules: list every
configured tenant/adapter pair, keep only adapters declaring support for the
sync kind, skip in-flight/fresh rows, then dispatch through the existing
orchestration or background worker path.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.core.database.database_session import get_db_session
from src.core.database.repositories.adapter_config import AdapterConfigAdminRepository
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services._scheduler_lifecycle import cancel_scheduler_task
from src.services.adapter_sync_orchestration import (
    KIND_INVENTORY,
    KIND_REPORTING,
    SyncExecutionResult,
    SyncKind,
    enqueue_adapter_sync,
    execute_adapter_sync,
)
from src.services.catalog_sync_helpers import dispatch_limited
from src.services.sync_scheduling_view import (
    GUIDANCE_STALE_AFTER,
    GUIDANCE_SYNC_KINDS,
    REPORTING_STALE_AFTER,
    _capability_flag,
)

logger = logging.getLogger(__name__)

# Matches the legacy cron default: every six hours unless the tenant
# overrides ``Tenant.sync_cadence_minutes``.
DEFAULT_INVENTORY_SYNC_CADENCE_MINUTES = 360


@dataclass(frozen=True)
class EligibleSync:
    tenant_id: str
    adapter_type: str
    sync_kind: SyncKind
    sync_cadence_minutes: int | None = None


def _env_seconds(name: str, default: int) -> int:
    return int(os.getenv(name) or str(default))


REPORTING_INTERVAL_SECONDS = _env_seconds("ADAPTER_REPORTING_SYNC_INTERVAL", 3600)
INVENTORY_GUIDANCE_INTERVAL_SECONDS = _env_seconds("ADAPTER_INVENTORY_GUIDANCE_SYNC_INTERVAL", 3600)
MAX_CONCURRENT_SYNC_DISPATCHES = int(
    os.getenv("ADAPTER_SYNC_MAX_CONCURRENT_TENANTS") or os.getenv("ADAPTER_REPORTING_MAX_CONCURRENT_TENANTS") or "3"
)


class AdapterSyncScheduler:
    """Fixed-interval scheduler for one or more adapter sync kinds."""

    def __init__(
        self,
        *,
        name: str,
        sync_kinds: Iterable[SyncKind],
        interval_seconds: int,
    ) -> None:
        self.name = name
        self.sync_kinds = tuple(sync_kinds)
        self.interval_seconds = interval_seconds
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.is_running:
                logger.warning("%s scheduler is already running", self.name)
                return
            self.is_running = True
            self._task = asyncio.create_task(self._run())
            logger.info("%s scheduler started (interval=%ss)", self.name, self.interval_seconds)

    async def stop(self) -> None:
        async with self._lock:
            if not self.is_running:
                return
            self.is_running = False
            await cancel_scheduler_task(self._task)
            logger.info("%s scheduler stopped", self.name)

    async def _run(self) -> None:
        """Main loop. First iteration runs immediately after startup."""
        while self.is_running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("%s scheduler iteration failed", self.name)
            await asyncio.sleep(self.interval_seconds)

    async def run_once(self, *, now: datetime | None = None) -> list[str]:
        snapshot = now or datetime.now(UTC)
        eligible = _list_eligible_syncs(snapshot, self.sync_kinds)
        if not eligible:
            return []

        dispatched = await dispatch_limited(
            eligible,
            max_concurrent=MAX_CONCURRENT_SYNC_DISPATCHES,
            dispatch=_dispatch,
        )

        logger.info(
            "%s sync cycle complete: dispatched=%d eligible=%d",
            self.name,
            len(dispatched),
            len(eligible),
        )
        return dispatched


class AdapterReportingSyncScheduler(AdapterSyncScheduler):
    """Compatibility wrapper for the original reporting-only scheduler."""

    def __init__(self) -> None:
        super().__init__(
            name="Adapter reporting sync",
            sync_kinds=(KIND_REPORTING,),
            interval_seconds=REPORTING_INTERVAL_SECONDS,
        )


class AdapterInventoryGuidanceSyncScheduler(AdapterSyncScheduler):
    """Runs inventory and guidance syncs through server-owned lifecycle hooks."""

    def __init__(self) -> None:
        super().__init__(
            name="Adapter inventory/guidance sync",
            sync_kinds=(KIND_INVENTORY, *GUIDANCE_SYNC_KINDS),
            interval_seconds=INVENTORY_GUIDANCE_INTERVAL_SECONDS,
        )


async def _dispatch(item: EligibleSync) -> str | None:
    try:
        if item.sync_kind == KIND_REPORTING:
            return await _execute_reporting_sync(item)
        return await asyncio.to_thread(_enqueue_non_reporting_sync, item)
    except Exception:
        logger.exception(
            "Scheduled %s sync dispatch crashed for tenant=%s adapter=%s",
            item.sync_kind,
            item.tenant_id,
            item.adapter_type,
        )
        return None


async def _execute_reporting_sync(item: EligibleSync) -> str | None:
    result = await asyncio.to_thread(
        execute_adapter_sync,
        tenant_id=item.tenant_id,
        adapter_type=item.adapter_type,
        sync_kind=item.sync_kind,
        triggered_by="scheduler_reporting",
    )
    if result is None:
        return None
    _log_unsuccessful_result(item, result)
    return result.sync_id


def _enqueue_non_reporting_sync(item: EligibleSync) -> str | None:
    run_kwargs = _scheduled_run_kwargs(item)
    return enqueue_adapter_sync(
        tenant_id=item.tenant_id,
        adapter_type=item.adapter_type,
        sync_kind=item.sync_kind,
        triggered_by=f"scheduler_{item.sync_kind}",
        run_kwargs=run_kwargs,
    )


def _scheduled_run_kwargs(item: EligibleSync) -> dict[str, str] | None:
    if item.sync_kind == KIND_INVENTORY and item.adapter_type == "google_ad_manager":
        return {"sync_mode": "full"}
    return None


def _log_unsuccessful_result(item: EligibleSync, result: SyncExecutionResult) -> None:
    if result.succeeded:
        return
    level = logging.INFO if result.scope_pending else logging.WARNING
    logger.log(
        level,
        "Scheduled %s sync did not succeed: tenant=%s adapter=%s scope_pending=%s errors=%s",
        item.sync_kind,
        item.tenant_id,
        item.adapter_type,
        result.scope_pending,
        list(result.errors.keys()),
    )


def _list_eligible_syncs(now: datetime, sync_kinds: Iterable[SyncKind]) -> list[EligibleSync]:
    with get_db_session() as session:
        pairs = AdapterConfigAdminRepository(session).list_all()
        supported = [
            EligibleSync(
                tenant_id=p.tenant_id,
                adapter_type=p.adapter_type,
                sync_kind=kind,
                sync_cadence_minutes=p.sync_cadence_minutes,
            )
            for p in pairs
            for kind in sync_kinds
            if p.sync_ready and _capability_flag(p.adapter_type, kind)
        ]
        if not supported:
            return []

        triples = [(s.tenant_id, s.adapter_type, s.sync_kind) for s in supported]
        latest = SyncJobAdminRepository(session).latest_for_triples(triples)

    return [
        item
        for item in supported
        if _is_eligible(
            item,
            latest.get((item.tenant_id, item.adapter_type, item.sync_kind)),
            now,
        )
    ]


def _is_eligible(item: EligibleSync, last, now: datetime) -> bool:
    if last is None:
        return True
    if last.status in ("pending", "queued", "running"):
        return False
    if last.status == "completed" and last.completed_at is not None:
        completed_at = last.completed_at
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=UTC)
        if (now - completed_at) < _stale_after(item):
            return False
    return True


def _stale_after(item: EligibleSync) -> timedelta:
    if item.sync_kind == KIND_REPORTING:
        return REPORTING_STALE_AFTER
    if item.sync_kind == KIND_INVENTORY:
        cadence = item.sync_cadence_minutes or DEFAULT_INVENTORY_SYNC_CADENCE_MINUTES
        return timedelta(minutes=cadence)
    return GUIDANCE_STALE_AFTER


def _list_eligible_tenants(now: datetime) -> list[tuple[str, str]]:
    """Back-compatible reporting helper used by older tests/imports."""
    return [(i.tenant_id, i.adapter_type) for i in _list_eligible_syncs(now, (KIND_REPORTING,))]


_reporting_scheduler: AdapterReportingSyncScheduler | None = None
_inventory_guidance_scheduler: AdapterInventoryGuidanceSyncScheduler | None = None


def get_adapter_reporting_sync_scheduler() -> AdapterReportingSyncScheduler:
    global _reporting_scheduler
    if _reporting_scheduler is None:
        _reporting_scheduler = AdapterReportingSyncScheduler()
    return _reporting_scheduler


def get_adapter_inventory_guidance_sync_scheduler() -> AdapterInventoryGuidanceSyncScheduler:
    global _inventory_guidance_scheduler
    if _inventory_guidance_scheduler is None:
        _inventory_guidance_scheduler = AdapterInventoryGuidanceSyncScheduler()
    return _inventory_guidance_scheduler


async def start_adapter_reporting_sync_scheduler() -> None:
    await get_adapter_reporting_sync_scheduler().start()


async def stop_adapter_reporting_sync_scheduler() -> None:
    await get_adapter_reporting_sync_scheduler().stop()


async def start_adapter_inventory_guidance_sync_scheduler() -> None:
    await get_adapter_inventory_guidance_sync_scheduler().start()


async def stop_adapter_inventory_guidance_sync_scheduler() -> None:
    await get_adapter_inventory_guidance_sync_scheduler().stop()
