"""Integration tests for the cross-tenant scheduling view (#382 Stage 4).

Covers the DB-touching paths:
  - ``SyncJobAdminRepository`` cross-tenant queries
  - ``AdapterConfigAdminRepository`` tenant-name join
  - ``build_scheduling_matrix`` end-to-end against real Postgres

Sync history is seeded via :func:`execute_sync` rather than direct
``session.add()`` calls so the rows match what the orchestrator would
write at runtime (and we don't trip the repository-pattern guard).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.base import AdapterSyncResult
from src.core.database.database_session import get_db_session
from src.core.database.repositories.adapter_config import AdapterConfigAdminRepository
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services.adapter_sync_orchestration import KIND_INVENTORY, KIND_REPORTING, execute_sync
from src.services.sync_scheduling_view import build_scheduling_matrix
from tests.factories import AdapterConfigFactory, TenantFactory
from tests.helpers.sync_orchestration import make_mock_adapter

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_completed_sync(
    *,
    tenant_id: str,
    adapter_name: str,
    sync_kind: str,
    counts: dict[str, int] | None = None,
):
    """Drive one successful run through the orchestrator so the SyncJob
    row gets written by the production write path."""
    started = datetime.now(UTC)
    finished = datetime.now(UTC)
    if sync_kind == KIND_INVENTORY:
        adapter = make_mock_adapter(
            supports_inventory=True,
            adapter_name=adapter_name,
            inventory_result=AdapterSyncResult(
                sync_kind=KIND_INVENTORY,
                started_at=started,
                finished_at=finished,
                succeeded=True,
                counts=counts or {"site": 1},
            ),
        )
    else:
        adapter = make_mock_adapter(
            supports_reporting=True,
            adapter_name=adapter_name,
            reporting_result=AdapterSyncResult(
                sync_kind=KIND_REPORTING,
                started_at=started,
                finished_at=finished,
                succeeded=True,
                counts=counts or {"placements": 1},
            ),
        )

    return execute_sync(
        adapter=adapter,
        tenant_id=tenant_id,
        sync_kind=sync_kind,
        triggered_by="test_seed",
    )


class TestAdapterConfigAdminRepositoryListAll:
    def test_joins_tenant_name_and_orders_by_name(self, factory_session):
        tb = TenantFactory(tenant_id="t_b", name="B Corp")
        ta = TenantFactory(tenant_id="t_a", name="A Corp")
        AdapterConfigFactory(tenant=ta, adapter_type="freewheel")
        AdapterConfigFactory(tenant=tb, adapter_type="google_ad_manager")

        with get_db_session() as session:
            rows = AdapterConfigAdminRepository(session).list_all()

        # Filter to just our tenants (other test fixtures may leak in).
        rows = [r for r in rows if r.tenant_id in {"t_a", "t_b"}]
        assert [r.tenant_id for r in rows] == ["t_a", "t_b"]
        assert rows[0].tenant_name == "A Corp"
        assert rows[0].adapter_type == "freewheel"
        assert rows[1].adapter_type == "google_ad_manager"


class TestSyncJobAdminRepositoryLatest:
    def test_returns_freshest_row_per_triple(self, factory_session):
        TenantFactory(tenant_id="t_lpk")

        _seed_completed_sync(tenant_id="t_lpk", adapter_name="freewheel", sync_kind=KIND_INVENTORY)
        # Second run should win.
        second = _seed_completed_sync(tenant_id="t_lpk", adapter_name="freewheel", sync_kind=KIND_INVENTORY)

        with get_db_session() as session:
            latest = SyncJobAdminRepository(session).latest_per_kind()

        assert latest[("t_lpk", "freewheel", KIND_INVENTORY)].sync_id == second.sync_id

    def test_list_recent_orders_desc(self, factory_session):
        TenantFactory(tenant_id="t_recent")
        first = _seed_completed_sync(tenant_id="t_recent", adapter_name="freewheel", sync_kind=KIND_INVENTORY)
        second = _seed_completed_sync(tenant_id="t_recent", adapter_name="freewheel", sync_kind=KIND_REPORTING)

        with get_db_session() as session:
            jobs = SyncJobAdminRepository(session).list_recent(limit=10)

        # Filter to just our tenant.
        jobs = [j for j in jobs if j.tenant_id == "t_recent"]
        assert len(jobs) == 2
        # Second-seeded is newer → comes first.
        assert jobs[0].sync_id == second.sync_id
        assert jobs[1].sync_id == first.sync_id


class TestBuildSchedulingMatrixEndToEnd:
    """Full path: configs → expected triples → latest job lookup → rows."""

    def test_freewheel_tenant_yields_both_kinds_with_inventory_filled(self, factory_session):
        t = TenantFactory(tenant_id="t_e2e", name="E2E Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        seeded = _seed_completed_sync(tenant_id="t_e2e", adapter_name="freewheel", sync_kind=KIND_INVENTORY)

        with get_db_session() as session:
            rows = build_scheduling_matrix(session)

        rows = [r for r in rows if r.tenant_id == "t_e2e"]
        assert {r.sync_kind for r in rows} == {KIND_INVENTORY, KIND_REPORTING}

        inv = next(r for r in rows if r.sync_kind == KIND_INVENTORY)
        rep = next(r for r in rows if r.sync_kind == KIND_REPORTING)

        assert inv.last_sync_id == seeded.sync_id
        assert inv.stale is False
        assert inv.never_run is False

        # No reporting job yet → never_run + stale (action needed).
        assert rep.never_run is True
        assert rep.stale is True

    def test_gam_tenant_only_yields_inventory_row(self, factory_session):
        # GAM declares supports_reporting_sync=False so the matrix
        # must NOT fabricate a reporting slot for it.
        t = TenantFactory(tenant_id="t_gam_only", name="GAM Co")
        AdapterConfigFactory(tenant=t, adapter_type="google_ad_manager")

        with get_db_session() as session:
            rows = build_scheduling_matrix(session)

        rows = [r for r in rows if r.tenant_id == "t_gam_only"]
        assert len(rows) == 1
        assert rows[0].sync_kind == KIND_INVENTORY

    def test_mock_tenant_yields_no_rows(self, factory_session):
        # Mock declares neither — matrix should skip it entirely.
        t = TenantFactory(tenant_id="t_mock_only", name="Mock Co")
        AdapterConfigFactory(tenant=t, adapter_type="mock")

        with get_db_session() as session:
            rows = build_scheduling_matrix(session)

        rows = [r for r in rows if r.tenant_id == "t_mock_only"]
        assert rows == []


class TestStaleVerdictAgainstOldHistory:
    def test_completed_run_older_than_threshold_renders_stale(self, factory_session):
        # We can't backdate the orchestrator run, but we can verify the
        # matrix's "stale=True" verdict via the freshness threshold by
        # using a kind whose stale window is short (reporting = 2h) and
        # ensuring the inserted row carries an old completed_at.
        #
        # We seed via the orchestrator then mutate completed_at directly
        # — this is one of the few cases where touching the SyncJob row
        # is the cleanest way to test the time-window logic without
        # introducing a clock-injection seam in production code.
        t = TenantFactory(tenant_id="t_stale_check", name="Stale Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")
        seeded = _seed_completed_sync(tenant_id="t_stale_check", adapter_name="freewheel", sync_kind=KIND_REPORTING)

        # Backdate the completed_at past the 2h reporting threshold.
        from sqlalchemy import select

        from src.core.database.models import SyncJob

        with get_db_session() as session:
            row = session.scalars(select(SyncJob).filter_by(sync_id=seeded.sync_id)).first()
            row.completed_at = datetime.now(UTC) - timedelta(hours=3)
            session.commit()

        with get_db_session() as session:
            rows = build_scheduling_matrix(session)

        rep = next(r for r in rows if r.tenant_id == "t_stale_check" and r.sync_kind == KIND_REPORTING)
        assert rep.stale is True
        assert rep.never_run is False
