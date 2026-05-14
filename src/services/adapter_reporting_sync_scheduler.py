"""Scheduled cross-tenant reporting sync (#382 Stage 5).

The :class:`AdapterReportingSyncScheduler` periodically walks every
configured ``(tenant, adapter)`` pair whose adapter declares
``supports_reporting_sync=True`` and runs the reporting sync through
the shared orchestrator from Stage 3. The same SyncJob rows show up
in the Stage 4 ``/admin/scheduling`` view.

Cadence: hourly by default (matches the reporting freshness threshold
in :mod:`src.services.sync_scheduling_view`). Configurable via the
``ADAPTER_REPORTING_SYNC_INTERVAL`` env var for tests.

Skip-when-fresh: if the most recent successful reporting run completed
less than ``REPORTING_STALE_AFTER`` ago, skip the tenant for this
cycle. Prevents thundering-herd retries during startup loops or when
the upstream API is rate-limiting.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from src.core.database.database_session import get_db_session
from src.core.database.repositories.adapter_config import AdapterConfigAdminRepository
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services._scheduler_lifecycle import cancel_scheduler_task
from src.services.adapter_sync_orchestration import KIND_REPORTING, execute_adapter_sync
from src.services.sync_scheduling_view import REPORTING_STALE_AFTER, _capability_flag

logger = logging.getLogger(__name__)

# Hourly by default. Reporting feeds the delivery pipeline so 1h is the
# right cadence: any longer and the freshness threshold (2h) bites; any
# shorter and we're spamming upstream APIs that bill per-call.
SLEEP_INTERVAL_SECONDS = int(os.getenv("ADAPTER_REPORTING_SYNC_INTERVAL") or "3600")


class AdapterReportingSyncScheduler:
    """Fixed-interval scheduler that runs reporting syncs for every
    tenant whose adapter supports it.

    Lifecycle mirrors :class:`DeliveryWebhookScheduler`:
    start / stop are async, the loop runs in an ``asyncio.Task``, and
    cancellation is graceful. ``run_once()`` is the unit-testable
    iteration — production calls it on a loop with sleep between cycles.
    """

    def __init__(self) -> None:
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.is_running:
                logger.warning("Adapter reporting sync scheduler is already running")
                return
            self.is_running = True
            self._task = asyncio.create_task(self._run())
            logger.info("Adapter reporting sync scheduler started (interval=%ss)", SLEEP_INTERVAL_SECONDS)

    async def stop(self) -> None:
        async with self._lock:
            if not self.is_running:
                return
            self.is_running = False
            await cancel_scheduler_task(self._task)
            logger.info("Adapter reporting sync scheduler stopped")

    async def _run(self) -> None:
        """Main loop. First iteration runs immediately so the dashboard
        has fresh data right after boot."""
        while self.is_running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Adapter reporting sync scheduler iteration failed")
            await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

    async def run_once(self, *, now: datetime | None = None) -> list[str]:
        """Execute one cycle: dispatch reporting syncs for every eligible
        ``(tenant, adapter)`` pair.

        Returns the list of sync_ids actually triggered (for telemetry +
        tests). Skipped tenants are NOT in the list — fresh ones, ones
        without reporting capability, and ones with no AdapterConfig
        all return without dispatching.

        ``execute_adapter_sync`` is synchronous (DB-bound); we run it on a
        thread to avoid blocking the event loop when many tenants exist.
        """
        snapshot = now or datetime.now(UTC)
        eligible = _list_eligible_tenants(snapshot)
        if not eligible:
            return []

        dispatched: list[str] = []
        for tenant_id, adapter_type in eligible:
            try:
                result = await asyncio.to_thread(
                    execute_adapter_sync,
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    sync_kind=KIND_REPORTING,
                    triggered_by="scheduler_reporting",
                )
            except Exception:
                # ``execute_adapter_sync`` catches adapter exceptions
                # internally and persists status=failed, so reaching here
                # means a more fundamental failure (DB connection lost,
                # adapter constructor crashed before the orchestrator
                # could wrap it, etc). Log + continue so one bad tenant
                # doesn't take down the cycle for everyone.
                logger.exception(
                    "Reporting sync dispatch crashed for tenant=%s adapter=%s",
                    tenant_id,
                    adapter_type,
                )
                continue

            if result is None:
                # Tenant had no AdapterConfig matching adapter_type by the
                # time we tried to run — usually a race against an admin
                # disabling the adapter. Skip silently.
                continue

            dispatched.append(result.sync_id)
            if not result.succeeded:
                # Surface scope_pending vs generic failure at INFO/WARNING
                # so operators reading the log can tell which tenants
                # need IAM grants vs which need real debugging.
                level = logging.INFO if result.scope_pending else logging.WARNING
                logger.log(
                    level,
                    "Scheduled reporting sync did not succeed: tenant=%s adapter=%s scope_pending=%s errors=%s",
                    tenant_id,
                    adapter_type,
                    result.scope_pending,
                    list(result.errors.keys()),
                )

        logger.info(
            "Reporting sync cycle complete: dispatched=%d eligible=%d",
            len(dispatched),
            len(eligible),
        )
        return dispatched


def _list_eligible_tenants(now: datetime) -> list[tuple[str, str]]:
    """Return ``(tenant_id, adapter_type)`` pairs whose adapter supports
    reporting AND whose last successful reporting sync is older than the
    freshness threshold (or never ran).

    Same skip-when-fresh contract as the ``/admin/scheduling`` matrix:
    a failed last run does NOT count as fresh — the cache wasn't
    refreshed, so the scheduler should try again next cycle. A run
    that's currently ``running`` also doesn't count: it might be stuck
    or making progress, and re-dispatching while another sync is live
    could blow the upstream rate limit.
    """
    with get_db_session() as session:
        pairs = AdapterConfigAdminRepository(session).list_all()
        reporting_pairs = [
            (p.tenant_id, p.adapter_type) for p in pairs if _capability_flag(p.adapter_type, KIND_REPORTING)
        ]
        if not reporting_pairs:
            return []

        triples = [(tid, atype, KIND_REPORTING) for tid, atype in reporting_pairs]
        latest = SyncJobAdminRepository(session).latest_for_triples(triples)

    eligible: list[tuple[str, str]] = []
    for tenant_id, adapter_type in reporting_pairs:
        last = latest.get((tenant_id, adapter_type, KIND_REPORTING))
        if last is None:
            eligible.append((tenant_id, adapter_type))
            continue
        if last.status in ("running", "queued"):
            # In-flight — skip. ``queued`` rows come from the async
            # /admin/scheduling Run Now dispatch; the daemon thread
            # will transition them to running shortly, but until then
            # we must not race another sync against the same triple.
            continue
        if last.status == "completed" and last.completed_at is not None:
            if (now - last.completed_at) <= REPORTING_STALE_AFTER:
                # Fresh enough — skip until threshold expires.
                continue
        eligible.append((tenant_id, adapter_type))

    return eligible


# Module-level singleton matches DeliveryWebhookScheduler conventions so
# the serve()-lifespan wiring in core.main has one obvious entry point.
_scheduler: AdapterReportingSyncScheduler | None = None


def get_adapter_reporting_sync_scheduler() -> AdapterReportingSyncScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AdapterReportingSyncScheduler()
    return _scheduler


async def start_adapter_reporting_sync_scheduler() -> None:
    """Boot the scheduler — wired into serve()'s on_startup hook."""
    await get_adapter_reporting_sync_scheduler().start()


async def stop_adapter_reporting_sync_scheduler() -> None:
    """Stop the scheduler — wired into serve()'s on_shutdown hook."""
    await get_adapter_reporting_sync_scheduler().stop()
