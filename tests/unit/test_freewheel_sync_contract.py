"""FreeWheelAdapter implements the uniform sync contract from Stage 1
of #382. These tests pin the conversion from FW-internal sync types
(:class:`FreeWheelInventorySync.SyncResult` + :class:`ReportingSyncResult`)
to the shared :class:`AdapterSyncResult` shape the shared
``AdapterSyncScheduler`` will consume.

We focus on the contract translation, not the underlying syncers
themselves — those have their own coverage in test_freewheel_inventory_sync
+ test_freewheel_reporting_cache.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.adapters.base import AdapterSyncResult
from src.adapters.freewheel import FreeWheelAdapter


@pytest.fixture
def mock_principal():
    p = MagicMock()
    p.principal_id = "p1"
    p.get_adapter_id.return_value = "1356511"
    p.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}
    return p


class TestCapabilities:
    """FW declares both inventory + reporting sync support."""

    def test_freewheel_declares_both_sync_capabilities(self, mock_principal):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        assert adapter.capabilities.supports_inventory_sync is True
        assert adapter.capabilities.supports_reporting_sync is True


class TestRunInventorySyncDryRun:
    """Dry-run can't actually call FW; surface that as a clean
    AdapterSyncResult so the scheduler doesn't try to interpret a
    raised exception."""

    def test_returns_soft_failed_result(self, mock_principal):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        result = adapter.run_inventory_sync()
        assert isinstance(result, AdapterSyncResult)
        assert result.sync_kind == "inventory"
        assert result.succeeded is False
        assert "dry-run" in result.errors["adapter"].lower()


class TestRunReportingSyncDryRun:
    def test_returns_soft_failed_result(self, mock_principal):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        result = adapter.run_reporting_sync()
        assert isinstance(result, AdapterSyncResult)
        assert result.sync_kind == "reporting"
        assert result.succeeded is False


class TestRunReportingSyncScopePending:
    """When the upstream Reporting API scope is still pending,
    FreeWheelReportingSync raises ReportingScopeNotGranted. The adapter
    catches that and returns a soft-failed AdapterSyncResult with
    metadata.scope_pending=True so the scheduler can surface it
    differently from a generic failure."""

    def test_scope_pending_surfaces_in_metadata(self, mock_principal, monkeypatch):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=False, tenant_id="t1")

        from src.adapters.freewheel.reporting_sync import ReportingScopeNotGranted

        class _DenySync:
            def __init__(self, *_, **__):
                pass

            def run(self, **kwargs):
                raise ReportingScopeNotGranted()

        monkeypatch.setattr("src.adapters.freewheel.reporting_sync.FreeWheelReportingSync", _DenySync)
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        result = adapter.run_reporting_sync()
        assert result.sync_kind == "reporting"
        assert result.succeeded is False
        assert result.metadata.get("scope_pending") is True
        assert "scope" in result.errors


class TestFreshnessAccessors:
    """The freshness accessors read from the existing FW repo methods —
    smoke-test that the adapter wires them up rather than testing the
    repo logic itself (covered elsewhere)."""

    def test_inventory_freshness_reads_from_inventory_repo(self, mock_principal, monkeypatch):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        expected = datetime.now(UTC)
        mock_repo = MagicMock()
        mock_repo.latest_sync_at.return_value = expected
        from tests.helpers.freewheel_adapter_patches import patch_freewheel_db

        patch_freewheel_db(monkeypatch, mock_repo)

        assert adapter.latest_inventory_sync_at() == expected

    def test_reporting_freshness_reads_from_placement_stats_repo(self, mock_principal, monkeypatch):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        expected = datetime.now(UTC)
        mock_repo = MagicMock()
        mock_repo.latest_sync_at.return_value = expected
        monkeypatch.setattr(
            "src.core.database.repositories.freewheel_placement_stats.FreeWheelPlacementStatsRepository",
            lambda session, tenant_id: mock_repo,
        )
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        assert adapter.latest_reporting_sync_at() == expected
