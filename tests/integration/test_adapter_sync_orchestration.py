"""Integration tests for the shared adapter sync orchestration (#382 Stage 3).

The pure-contract tests (capability gating, sync_kind validation) live
in tests/unit/test_adapter_sync_orchestration.py. This file covers the
DB-touching paths: SyncJob persistence, status transitions, error
message stamping.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.adapters.base import AdapterSyncResult
from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob
from src.services.adapter_sync_orchestration import (
    KIND_INVENTORY,
    KIND_PRICE_GUIDANCE,
    KIND_REPORTING,
    SyncExecutionResult,
    execute_sync,
)
from tests.factories import TenantFactory
from tests.helpers.sync_orchestration import make_mock_adapter as _mock_adapter

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestSuccessfulRunPersistsSyncJob:
    """Happy path: adapter returns AdapterSyncResult(succeeded=True),
    a SyncJob row gets written with status=completed and the per-kind
    counts stashed in progress."""

    def test_inventory_success_writes_completed_sync_job(self, factory_session):
        TenantFactory(tenant_id="t_sync_success")

        start = datetime.now(UTC)
        adapter = _mock_adapter(
            supports_inventory=True,
            inventory_result=AdapterSyncResult(
                sync_kind="inventory",
                started_at=start,
                finished_at=datetime.now(UTC),
                succeeded=True,
                counts={"site": 29, "site_section": 51},
            ),
        )

        result = execute_sync(
            adapter=adapter,
            tenant_id="t_sync_success",
            sync_kind=KIND_INVENTORY,
            triggered_by="admin_button",
        )

        assert isinstance(result, SyncExecutionResult)
        assert result.succeeded is True
        assert result.counts == {"site": 29, "site_section": 51}

        with get_db_session() as session:
            row = session.scalar(select(SyncJob).filter_by(sync_id=result.sync_id))
            assert row is not None
            assert row.tenant_id == "t_sync_success"
            assert row.adapter_type == "_mock_test"
            assert row.sync_type == "inventory"
            assert row.status == "completed"
            assert row.completed_at is not None
            assert row.progress["counts"] == {"site": 29, "site_section": 51}
            assert row.error_message is None

    def test_price_guidance_success_writes_completed_sync_job(self, factory_session):
        TenantFactory(tenant_id="t_price_guidance_success")

        adapter = _mock_adapter(
            supports_price_guidance=True,
            price_guidance_result=AdapterSyncResult(
                sync_kind=KIND_PRICE_GUIDANCE,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                succeeded=True,
                counts={"products": 3},
            ),
        )

        result = execute_sync(
            adapter=adapter,
            tenant_id="t_price_guidance_success",
            sync_kind=KIND_PRICE_GUIDANCE,
            triggered_by="scheduler_price_guidance",
        )

        assert result.succeeded is True
        assert result.counts == {"products": 3}

        with get_db_session() as session:
            row = session.scalar(select(SyncJob).filter_by(sync_id=result.sync_id))
            assert row is not None
            assert row.sync_type == KIND_PRICE_GUIDANCE
            assert row.triggered_by == "scheduler_price_guidance"
            assert row.status == "completed"


class TestFailedRunMarksJobFailed:
    def test_failed_result_persists_first_error_to_error_message(self, factory_session):
        TenantFactory(tenant_id="t_sync_fail")

        adapter = _mock_adapter(
            supports_reporting=True,
            reporting_result=AdapterSyncResult(
                sync_kind="reporting",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                succeeded=False,
                errors={"scope": "Tier 1 reporting scope still pending"},
                metadata={"scope_pending": True},
            ),
        )

        result = execute_sync(
            adapter=adapter,
            tenant_id="t_sync_fail",
            sync_kind=KIND_REPORTING,
            triggered_by="scheduler",
        )

        assert result.succeeded is False
        assert result.scope_pending is True

        with get_db_session() as session:
            row = session.scalar(select(SyncJob).filter_by(sync_id=result.sync_id))
            assert row.status == "failed"
            assert "scope" in row.error_message
            assert row.progress["metadata"]["scope_pending"] is True


class TestEnqueueAdapterSyncReturnsImmediately:
    """``enqueue_adapter_sync`` validates synchronously and returns the
    pre-generated ``sync_id`` without waiting for the adapter call. The
    daemon thread is what actually runs the sync — covered indirectly
    by checking that the queued row transitions to a terminal state."""

    def test_pre_creates_queued_row_with_caller_attribution(self, factory_session):
        from src.services.adapter_sync_orchestration import enqueue_adapter_sync
        from tests.factories import AdapterConfigFactory

        t = TenantFactory(tenant_id="t_enq", name="Enq Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        # Patch get_adapter_class so the daemon thread builds a stub that
        # succeeds quickly — otherwise the FW adapter would try to reach
        # the real Publisher API.
        from unittest.mock import patch

        from src.adapters.base import AdapterCapabilities, AdapterSyncResult

        class _StubAdapter:
            adapter_name = "freewheel"
            capabilities = AdapterCapabilities(supports_reporting_sync=True)

            def __init__(self, *_args, **_kwargs):
                pass

            def run_reporting_sync(self, **_kwargs):
                return AdapterSyncResult(
                    sync_kind="reporting",
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                    succeeded=True,
                    counts={"placements": 3},
                )

        with patch("src.adapters.get_adapter_class", return_value=_StubAdapter):
            sync_id = enqueue_adapter_sync(
                tenant_id="t_enq",
                adapter_type="freewheel",
                sync_kind="reporting",
                triggered_by="admin_scheduling_ui",
                triggered_by_id="admin@example.com",
            )
            assert sync_id is not None
            assert sync_id.startswith("sync_")

            # Daemon thread may not have started yet; the queued row
            # must exist immediately so the UI can show it.
            with get_db_session() as session:
                row = session.scalar(select(SyncJob).filter_by(sync_id=sync_id))
                assert row is not None
                assert row.status in ("queued", "running", "completed")
                assert row.triggered_by == "admin_scheduling_ui"
                assert row.triggered_by_id == "admin@example.com"

            # Give the daemon thread a moment to complete.
            import time

            for _ in range(50):
                with get_db_session() as session:
                    row = session.scalar(select(SyncJob).filter_by(sync_id=sync_id))
                    if row.status in ("completed", "failed"):
                        break
                time.sleep(0.05)

            with get_db_session() as session:
                row = session.scalar(select(SyncJob).filter_by(sync_id=sync_id))
                assert row.status == "completed"

    def test_unconfigured_tenant_returns_none_without_creating_row(self, factory_session):
        from src.services.adapter_sync_orchestration import enqueue_adapter_sync

        TenantFactory(tenant_id="t_no_cfg")
        # No AdapterConfigFactory — tenant has no adapter configured.

        result = enqueue_adapter_sync(
            tenant_id="t_no_cfg",
            adapter_type="freewheel",
            sync_kind="reporting",
            triggered_by="test",
        )
        assert result is None

        # No SyncJob row should exist for this tenant.
        with get_db_session() as session:
            rows = session.scalars(select(SyncJob).filter_by(tenant_id="t_no_cfg")).all()
            assert rows == []


class TestAdapterRaisingIsCaughtAndPersisted:
    """Adapters SHOULD return AdapterSyncResult rather than raise — but
    if they do, the orchestration catches, persists status=failed, and
    returns a SyncExecutionResult so schedulers keep running."""

    def test_raised_exception_caught_and_persisted_as_failed(self, factory_session):
        TenantFactory(tenant_id="t_sync_raise")

        adapter = _mock_adapter(supports_inventory=True)
        adapter.run_inventory_sync.side_effect = ConnectionError("upstream is down")

        result = execute_sync(
            adapter=adapter,
            tenant_id="t_sync_raise",
            sync_kind=KIND_INVENTORY,
            triggered_by="test",
        )

        assert result.succeeded is False
        assert "upstream is down" in result.errors["adapter"]

        with get_db_session() as session:
            row = session.scalar(select(SyncJob).filter_by(sync_id=result.sync_id))
            assert row.status == "failed"
            assert "ConnectionError" in row.error_message
