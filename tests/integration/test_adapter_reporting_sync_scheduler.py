"""Integration tests for the scheduled reporting sync (#382 Stage 5).

Covers the cross-tenant DB-touching path:
  - ``_list_eligible_tenants`` driven by real AdapterConfig rows + real
    SyncJob history
  - ``run_once`` end-to-end against the real orchestrator (the orchestrator
    is mocked through to a stub adapter so we don't need live FW credentials)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.adapters.base import AdapterSyncResult
from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob
from src.services.adapter_reporting_sync_scheduler import (
    AdapterReportingSyncScheduler,
    _list_eligible_tenants,
)
from src.services.adapter_sync_orchestration import KIND_REPORTING, execute_sync
from tests.factories import AdapterConfigFactory, TenantFactory
from tests.helpers.sync_orchestration import make_mock_adapter

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_reporting_run(tenant_id: str, adapter_name: str):
    """Drive one successful reporting run through the orchestrator."""
    adapter = make_mock_adapter(
        supports_reporting=True,
        adapter_name=adapter_name,
        reporting_result=AdapterSyncResult(
            sync_kind=KIND_REPORTING,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            succeeded=True,
            counts={"placements": 1},
        ),
    )
    return execute_sync(
        adapter=adapter,
        tenant_id=tenant_id,
        sync_kind=KIND_REPORTING,
        triggered_by="test_seed",
    )


class TestListEligibleTenantsAgainstRealDb:
    def test_freewheel_tenant_with_no_history_is_eligible(self, factory_session):
        t = TenantFactory(tenant_id="t_fw_new", name="New FW")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        eligible = _list_eligible_tenants(datetime.now(UTC))
        assert ("t_fw_new", "freewheel") in eligible

    def test_mock_tenant_never_eligible(self, factory_session):
        t = TenantFactory(tenant_id="t_mock_new", name="Mock Co")
        AdapterConfigFactory(tenant=t, adapter_type="mock")

        eligible = _list_eligible_tenants(datetime.now(UTC))
        assert not any(tid == "t_mock_new" for tid, _ in eligible)

    def test_fresh_completed_run_filters_out_tenant(self, factory_session):
        t = TenantFactory(tenant_id="t_fresh_run", name="Fresh FW")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")
        _seed_reporting_run("t_fresh_run", "freewheel")

        eligible = _list_eligible_tenants(datetime.now(UTC))
        assert not any(tid == "t_fresh_run" for tid, _ in eligible)

    def test_old_completed_run_lets_tenant_through(self, factory_session):
        # Seed via orchestrator, then backdate completed_at past the
        # freshness threshold so the next cycle picks it back up.
        t = TenantFactory(tenant_id="t_old_run", name="Old FW")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")
        seeded = _seed_reporting_run("t_old_run", "freewheel")

        from sqlalchemy import select

        with get_db_session() as session:
            row = session.scalars(select(SyncJob).filter_by(sync_id=seeded.sync_id)).first()
            row.completed_at = datetime.now(UTC) - timedelta(hours=3)
            session.commit()

        eligible = _list_eligible_tenants(datetime.now(UTC))
        assert ("t_old_run", "freewheel") in eligible


class TestRunOnceEndToEnd:
    """``run_once`` calls the real orchestrator. We patch ``get_adapter_class``
    upstream so the orchestrator builds our stub instead of the real
    FreeWheelAdapter (which would need live credentials)."""

    @pytest.mark.asyncio
    async def test_run_once_writes_sync_job_rows_for_eligible_tenants(self, factory_session):
        t1 = TenantFactory(tenant_id="t_run_a", name="Run A")
        AdapterConfigFactory(tenant=t1, adapter_type="freewheel")
        t2 = TenantFactory(tenant_id="t_run_b", name="Run B")
        AdapterConfigFactory(tenant=t2, adapter_type="freewheel")

        # Stub adapter class: returns success quickly without touching FW.
        class _StubAdapter:
            adapter_name = "freewheel"
            capabilities = make_mock_adapter(supports_reporting=True).capabilities

            def __init__(self, *_args, **_kwargs):
                pass

            def run_reporting_sync(self, **_kwargs):
                return AdapterSyncResult(
                    sync_kind=KIND_REPORTING,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                    succeeded=True,
                    counts={"placements": 7},
                )

        with patch(
            "src.adapters.get_adapter_class",
            return_value=_StubAdapter,
        ):
            scheduler = AdapterReportingSyncScheduler()
            dispatched = await scheduler.run_once()

        # Both tenants got dispatched.
        assert len(dispatched) >= 2

        # And their SyncJob rows exist with status=completed.
        from sqlalchemy import select

        with get_db_session() as session:
            rows = session.scalars(
                select(SyncJob).where(
                    SyncJob.tenant_id.in_(["t_run_a", "t_run_b"]),
                    SyncJob.sync_type == KIND_REPORTING,
                    SyncJob.triggered_by == "scheduler_reporting",
                )
            ).all()
        assert {r.tenant_id for r in rows} == {"t_run_a", "t_run_b"}
        assert all(r.status == "completed" for r in rows)
