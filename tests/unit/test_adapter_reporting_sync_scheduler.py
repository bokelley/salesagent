"""Unit tests for the scheduled reporting sync (#382 Stage 5).

DB-touching coverage lives in
``tests/integration/test_adapter_reporting_sync_scheduler.py``. These
tests pin the pure-Python contracts: eligibility filtering, skip-when-fresh
logic, scheduler lifecycle.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.services.adapter_reporting_sync_scheduler import (
    AdapterReportingSyncScheduler,
    _list_eligible_tenants,
)
from src.services.adapter_sync_orchestration import KIND_REPORTING, SyncExecutionResult
from src.services.sync_scheduling_view import REPORTING_STALE_AFTER


def _pair(tenant_id, adapter_type):
    p = MagicMock()
    p.tenant_id = tenant_id
    p.adapter_type = adapter_type
    return p


def _patch_eligibility_layer(monkeypatch, *, pairs, latest_map):
    """Replace the three I/O seams used by ``_list_eligible_tenants``:
    the cross-tenant AdapterConfig listing, the latest-SyncJob map, and
    the DB-session context manager. Same three monkeypatches every test
    needed — extracted so each test reads as "given inputs, assert output."
    """
    import contextlib

    monkeypatch.setattr(
        "src.services.adapter_reporting_sync_scheduler.AdapterConfigAdminRepository.list_all",
        lambda self: pairs,
    )
    monkeypatch.setattr(
        "src.services.adapter_reporting_sync_scheduler.SyncJobAdminRepository.latest_for_triples",
        lambda self, triples: latest_map,
    )
    monkeypatch.setattr(
        "src.services.adapter_reporting_sync_scheduler.get_db_session",
        lambda: contextlib.nullcontext(MagicMock()),
    )


class TestListEligibleTenantsFiltering:
    """Capability gating: only adapters declaring reporting support are
    considered. Skip-when-fresh: if last completed run is newer than
    threshold, the pair is omitted."""

    def test_skips_adapters_without_reporting_capability(self, monkeypatch):
        # GAM declares reporting=False, FW declares reporting=True.
        # Latest map empty → only FW is eligible.
        _patch_eligibility_layer(
            monkeypatch,
            pairs=[_pair("t_gam", "google_ad_manager"), _pair("t_fw", "freewheel")],
            latest_map={},
        )

        eligible = _list_eligible_tenants(datetime.now(UTC))
        assert ("t_fw", "freewheel") in eligible
        assert ("t_gam", "google_ad_manager") not in eligible

    def test_skips_when_recent_completed_run(self, monkeypatch):
        now = datetime.now(UTC)
        recent_job = MagicMock()
        recent_job.status = "completed"
        recent_job.completed_at = now - (REPORTING_STALE_AFTER - timedelta(minutes=10))
        recent_job.sync_id = "sync_recent"

        _patch_eligibility_layer(
            monkeypatch,
            pairs=[_pair("t_fresh", "freewheel")],
            latest_map={("t_fresh", "freewheel", KIND_REPORTING): recent_job},
        )
        assert _list_eligible_tenants(now) == []

    def test_eligible_when_last_run_older_than_threshold(self, monkeypatch):
        now = datetime.now(UTC)
        stale_job = MagicMock()
        stale_job.status = "completed"
        stale_job.completed_at = now - (REPORTING_STALE_AFTER + timedelta(minutes=10))

        _patch_eligibility_layer(
            monkeypatch,
            pairs=[_pair("t_stale", "freewheel")],
            latest_map={("t_stale", "freewheel", KIND_REPORTING): stale_job},
        )
        assert _list_eligible_tenants(now) == [("t_stale", "freewheel")]

    def test_eligible_when_failed_last_run(self, monkeypatch):
        # Failed run = cache not refreshed = retry next cycle.
        now = datetime.now(UTC)
        failed_job = MagicMock()
        failed_job.status = "failed"
        failed_job.completed_at = now - timedelta(minutes=5)

        _patch_eligibility_layer(
            monkeypatch,
            pairs=[_pair("t_failed", "freewheel")],
            latest_map={("t_failed", "freewheel", KIND_REPORTING): failed_job},
        )
        assert _list_eligible_tenants(now) == [("t_failed", "freewheel")]

    def test_skips_when_currently_running(self, monkeypatch):
        # In-flight sync — don't pile on another dispatch.
        now = datetime.now(UTC)
        running_job = MagicMock()
        running_job.status = "running"
        running_job.completed_at = None

        _patch_eligibility_layer(
            monkeypatch,
            pairs=[_pair("t_running", "freewheel")],
            latest_map={("t_running", "freewheel", KIND_REPORTING): running_job},
        )
        assert _list_eligible_tenants(now) == []

    def test_skips_when_queued_or_running(self, monkeypatch):
        # ``queued`` rows come from :func:`enqueue_adapter_sync` —
        # transient, but the daemon thread will pick them up. The
        # scheduler must NOT dispatch a parallel sync against the same
        # triple while the queued row is still pending.
        now = datetime.now(UTC)
        queued_job = MagicMock()
        queued_job.status = "queued"
        queued_job.completed_at = None

        _patch_eligibility_layer(
            monkeypatch,
            pairs=[_pair("t_queued", "freewheel")],
            latest_map={("t_queued", "freewheel", KIND_REPORTING): queued_job},
        )
        assert _list_eligible_tenants(now) == []


class TestRunOnceDispatch:
    """``run_once`` walks eligible pairs and calls
    ``execute_adapter_sync`` for each. One bad tenant doesn't kill the
    cycle for the others."""

    @pytest.mark.asyncio
    async def test_dispatches_one_per_eligible_pair(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.adapter_reporting_sync_scheduler._list_eligible_tenants",
            lambda now: [("t1", "freewheel"), ("t2", "freewheel")],
        )

        calls: list[tuple[str, str]] = []

        def fake_exec(*, tenant_id, adapter_type, sync_kind, triggered_by):
            calls.append((tenant_id, adapter_type))
            assert sync_kind == KIND_REPORTING
            assert triggered_by == "scheduler_reporting"
            return SyncExecutionResult(sync_id=f"sync_{tenant_id}", sync_kind=KIND_REPORTING, succeeded=True)

        monkeypatch.setattr(
            "src.services.adapter_reporting_sync_scheduler.execute_adapter_sync",
            fake_exec,
        )

        scheduler = AdapterReportingSyncScheduler()
        dispatched = await scheduler.run_once()

        assert dispatched == ["sync_t1", "sync_t2"]
        assert calls == [("t1", "freewheel"), ("t2", "freewheel")]

    @pytest.mark.asyncio
    async def test_one_crashing_tenant_does_not_break_cycle(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.adapter_reporting_sync_scheduler._list_eligible_tenants",
            lambda now: [("t_boom", "freewheel"), ("t_ok", "freewheel")],
        )

        def fake_exec(*, tenant_id, adapter_type, sync_kind, triggered_by):
            if tenant_id == "t_boom":
                raise RuntimeError("DB pool exhausted")
            return SyncExecutionResult(sync_id="sync_ok", sync_kind=KIND_REPORTING, succeeded=True)

        monkeypatch.setattr(
            "src.services.adapter_reporting_sync_scheduler.execute_adapter_sync",
            fake_exec,
        )

        scheduler = AdapterReportingSyncScheduler()
        dispatched = await scheduler.run_once()

        # t_boom crashed but t_ok still dispatched.
        assert dispatched == ["sync_ok"]

    @pytest.mark.asyncio
    async def test_orchestrator_returning_none_is_silent_skip(self, monkeypatch):
        # Tenant disabled their adapter between matrix-read and dispatch.
        monkeypatch.setattr(
            "src.services.adapter_reporting_sync_scheduler._list_eligible_tenants",
            lambda now: [("t_gone", "freewheel")],
        )
        monkeypatch.setattr(
            "src.services.adapter_reporting_sync_scheduler.execute_adapter_sync",
            lambda **_: None,
        )

        scheduler = AdapterReportingSyncScheduler()
        assert await scheduler.run_once() == []


class TestSchedulerLifecycle:
    """``start`` / ``stop`` are async and cancellable. Double-start is
    a no-op so the lifespan hook is safe to call twice."""

    @pytest.mark.asyncio
    async def test_double_start_does_not_create_two_tasks(self, monkeypatch):
        # Pin the loop body so the task exits quickly.
        async def fast_loop(self):
            await asyncio.sleep(0)

        monkeypatch.setattr(AdapterReportingSyncScheduler, "_run", fast_loop)

        scheduler = AdapterReportingSyncScheduler()
        await scheduler.start()
        first_task = scheduler._task
        await scheduler.start()  # Second call → warning, no new task.
        assert scheduler._task is first_task
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self, monkeypatch):
        async def long_loop(self):
            while True:
                await asyncio.sleep(60)

        monkeypatch.setattr(AdapterReportingSyncScheduler, "_run", long_loop)

        scheduler = AdapterReportingSyncScheduler()
        await scheduler.start()
        assert scheduler.is_running is True
        await scheduler.stop()
        assert scheduler.is_running is False
        assert scheduler._task.cancelled() or scheduler._task.done()
