"""Unit tests for the scheduling view assembly (#382 Stage 4).

DB-touching tests live in
``tests/integration/test_sync_scheduling_view.py``. These cover the
pure-Python parts: capability filtering, stale verdict logic, dict
serialization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from src.core.database.repositories.adapter_config import TenantAdapterRow
from src.services.adapter_sync_orchestration import KIND_INVENTORY, KIND_REPORTING
from src.services.sync_scheduling_view import (
    INVENTORY_STALE_AFTER,
    REPORTING_STALE_AFTER,
    SchedulingRow,
    _build_row,
    _capability_flag,
    build_scheduling_matrix,
)


class TestCapabilityFlag:
    """``_capability_flag`` looks at the adapter class's declared
    AdapterCapabilities — instantiating the adapter would require
    credentials we don't have on the admin path."""

    def test_freewheel_supports_both(self):
        assert _capability_flag("freewheel", KIND_INVENTORY) is True
        assert _capability_flag("freewheel", KIND_REPORTING) is True

    def test_gam_supports_inventory_only(self):
        # GAM async inventory sync goes through background_sync_service;
        # reporting isn't a separate sync — line-item stats ride with inventory.
        assert _capability_flag("google_ad_manager", KIND_INVENTORY) is True
        assert _capability_flag("google_ad_manager", KIND_REPORTING) is False

    def test_mock_supports_neither(self):
        assert _capability_flag("mock", KIND_INVENTORY) is False
        assert _capability_flag("mock", KIND_REPORTING) is False

    def test_unknown_adapter_returns_false(self):
        assert _capability_flag("nonexistent", KIND_INVENTORY) is False


class TestBuildRowNeverRun:
    """A supported triple with no SyncJob → ``never_run=True`` and
    ``stale=True`` (action needed — admin should kick off a sync)."""

    def test_never_run_is_stale(self):
        row = _build_row(
            tenant_id="t1",
            tenant_name="Tenant One",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=None,
            now=datetime.now(UTC),
        )
        assert row.never_run is True
        assert row.stale is True
        assert row.last_status is None
        assert row.last_sync_id is None


class TestBuildRowFreshness:
    """Stale verdict: only ``completed`` runs count, and only when newer
    than the kind's threshold."""

    def _job(self, *, status, completed_at, started_at=None):
        job = MagicMock()
        job.status = status
        job.completed_at = completed_at
        job.started_at = started_at or completed_at
        job.sync_id = "sync_abc"
        job.error_message = None
        return job

    def test_recent_completed_inventory_is_fresh(self):
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=self._job(status="completed", completed_at=now - timedelta(hours=1)),
            now=now,
        )
        assert row.stale is False
        assert row.never_run is False

    def test_old_completed_inventory_is_stale(self):
        # Just past the 24h threshold.
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=self._job(
                status="completed",
                completed_at=now - (INVENTORY_STALE_AFTER + timedelta(minutes=1)),
            ),
            now=now,
        )
        assert row.stale is True

    def test_reporting_threshold_is_2h(self):
        # 90min < 2h → fresh. 150min > 2h → stale.
        now = datetime.now(UTC)
        fresh = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(status="completed", completed_at=now - timedelta(minutes=90)),
            now=now,
        )
        stale = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(
                status="completed",
                completed_at=now - (REPORTING_STALE_AFTER + timedelta(minutes=1)),
            ),
            now=now,
        )
        assert fresh.stale is False
        assert stale.stale is True

    def test_failed_run_is_always_stale(self):
        # Even a recently-failed run doesn't refresh the cache — the
        # underlying data is whatever it was before. Stale.
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(status="failed", completed_at=now - timedelta(minutes=5)),
            now=now,
        )
        assert row.stale is True

    def test_running_run_is_stale_until_it_completes(self):
        now = datetime.now(UTC)
        job = MagicMock()
        job.status = "running"
        job.started_at = now - timedelta(minutes=5)
        job.completed_at = None
        job.sync_id = "sync_abc"
        job.error_message = None
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=job,
            now=now,
        )
        assert row.stale is True


class TestSchedulingRowToDict:
    def test_dict_shape_matches_api_contract(self):
        now = datetime.now(UTC)
        row = SchedulingRow(
            tenant_id="t1",
            tenant_name="Tenant One",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            supported=True,
            last_status="completed",
            last_started_at=now - timedelta(minutes=10),
            last_completed_at=now - timedelta(minutes=8),
            last_sync_id="sync_abc",
            last_error_message=None,
            stale=False,
            never_run=False,
        )
        d = row.to_dict()
        assert d["tenant_id"] == "t1"
        assert d["sync_kind"] == "inventory"
        assert d["stale"] is False
        assert d["never_run"] is False
        assert d["last_sync_id"] == "sync_abc"
        assert d["freshness_age_seconds"] is not None
        assert d["freshness_age_seconds"] >= 0


class TestBuildMatrixSkipsUnsupportedKinds:
    """When a tenant's adapter declares ``supports_reporting_sync=False``
    (e.g. GAM), the matrix only contains an inventory row for it — the
    page doesn't fabricate a fake "reporting" slot.

    Driven by the AdapterCapabilities flag, not by config flags."""

    def test_gam_tenant_only_yields_inventory_row(self, monkeypatch):
        session = MagicMock()

        def fake_list_all(self):
            return [TenantAdapterRow(tenant_id="tg", tenant_name="G", adapter_type="google_ad_manager")]

        def fake_latest_for_triples(self, triples):
            return {}

        monkeypatch.setattr(
            "src.services.sync_scheduling_view.AdapterConfigAdminRepository.list_all",
            fake_list_all,
        )
        monkeypatch.setattr(
            "src.services.sync_scheduling_view.SyncJobAdminRepository.latest_for_triples",
            fake_latest_for_triples,
        )

        rows = build_scheduling_matrix(session)
        assert len(rows) == 1
        assert rows[0].sync_kind == KIND_INVENTORY
        assert rows[0].adapter_type == "google_ad_manager"
        assert rows[0].never_run is True
