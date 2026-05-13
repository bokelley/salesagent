"""Tests for the FreeWheel reporting cache scaffold.

Covers:
- Sync stub raises until FW scope is granted (so callers can detect it).
- ``get_packages_snapshot`` reads from the cache, returns ``None`` for missing rows.
- ``get_media_buy_delivery`` aggregates cache rows, falls back to empty
  response when the cache has no data for the IO yet.

These tests pin the read-side contract that day-of-scope work must satisfy.
The reporting client implementation itself is gated by the sync stub.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.adapters.freewheel import FreeWheelAdapter
from src.adapters.freewheel.reporting_sync import FreeWheelReportingSync, ReportingScopeNotGranted
from src.core.schemas import DeliveryStatus, ReportingPeriod


@pytest.fixture
def mock_principal():
    p = MagicMock()
    p.principal_id = "p1"
    p.get_adapter_id.return_value = "1356511"
    p.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}
    return p


def _make_stats_row(
    *,
    placement_id: str,
    insertion_order_id: str = "io_999",
    impressions: int = 1000,
    spend_micros: int = 5_000_000,
    completed_views: int | None = 800,
    clicks: int | None = None,
    currency: str = "EUR",
    delivery_status: str | None = "delivering",
    as_of: datetime | None = None,
):
    """Build a MagicMock that quacks like FreeWheelPlacementStats."""
    row = MagicMock()
    row.placement_id = placement_id
    row.insertion_order_id = insertion_order_id
    row.impressions = impressions
    row.spend_micros = spend_micros
    row.completed_views = completed_views
    row.clicks = clicks
    row.currency = currency
    row.delivery_status = delivery_status
    row.as_of = as_of or datetime.now(UTC) - timedelta(minutes=10)
    return row


class TestReportingSyncScopeHandling:
    """When the upstream API still IAM-denies the /reporting/* endpoints,
    :meth:`run` traps the 403 once at the top and raises
    :class:`ReportingScopeNotGranted` so schedulers see a clean signal."""

    def test_run_raises_scope_not_granted_on_forbidden(self):
        from src.adapters.freewheel._transport import FreeWheelForbiddenError

        client = MagicMock()
        # post_json on the underlying transport raises FreeWheelForbiddenError
        # for IAM-denied users — that's the production behaviour today.
        client._transport.post_json.side_effect = FreeWheelForbiddenError(
            "User is not authorized to access this resource"
        )
        sync = FreeWheelReportingSync(client=client, tenant_id="t1")
        with pytest.raises(ReportingScopeNotGranted):
            sync.run()

    def test_error_message_points_to_scope_request_doc(self):
        from src.adapters.freewheel._transport import FreeWheelForbiddenError

        client = MagicMock()
        client._transport.post_json.side_effect = FreeWheelForbiddenError("denied")
        sync = FreeWheelReportingSync(client=client, tenant_id="t1")
        try:
            sync.run()
        except ReportingScopeNotGranted as exc:
            assert "docs/adapters/freewheel/README.md" in str(exc)


class TestGetPackagesSnapshot:
    """Read path: cache hit returns a Snapshot, cache miss returns None."""

    def test_returns_none_when_placement_id_is_none(self, mock_principal):
        """If we never pushed the package to FW, there's no placement_id;
        emit None rather than fabricating a snapshot."""
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        out = adapter.get_packages_snapshot([("media_buy_1", "pkg_a", None)])
        assert out == {"media_buy_1": {"pkg_a": None}}

    def test_returns_none_when_cache_has_no_row(self, mock_principal, monkeypatch):
        """Sync isn't running yet → cache is empty → callers see None."""
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")

        mock_repo = MagicMock()
        mock_repo.get_by_placement_ids.return_value = {}

        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.FreeWheelPlacementStatsRepository",
            lambda session, tenant_id: mock_repo,
        )
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        out = adapter.get_packages_snapshot([("media_buy_1", "pkg_a", "12345")])
        assert out["media_buy_1"]["pkg_a"] is None

    def test_returns_snapshot_when_cache_has_row(self, mock_principal, monkeypatch):
        """Happy path: cache row → AdCP Snapshot with all derived fields."""
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")

        row = _make_stats_row(
            placement_id="12345",
            impressions=10_000,
            spend_micros=50_000_000,  # 50 EUR
            completed_views=8_500,
            currency="EUR",
            delivery_status="delivering",
        )
        mock_repo = MagicMock()
        mock_repo.get_by_placement_ids.return_value = {"12345": row}

        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.FreeWheelPlacementStatsRepository",
            lambda session, tenant_id: mock_repo,
        )
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        out = adapter.get_packages_snapshot([("media_buy_1", "pkg_a", "12345")])
        snapshot = out["media_buy_1"]["pkg_a"]
        assert snapshot is not None
        assert snapshot.impressions == 10_000.0
        assert snapshot.spend == 50.0
        assert snapshot.currency == "EUR"
        assert snapshot.delivery_status == DeliveryStatus.delivering
        # Staleness derived from row.as_of, must be non-negative
        assert snapshot.staleness_seconds >= 0


class TestGetMediaBuyDelivery:
    """Read path: aggregate cache rows for an IO into one delivery response."""

    def test_empty_cache_raises_data_unavailable(self, mock_principal, monkeypatch):
        """Empty cache must raise so the impl layer can surface
        ``data_unavailable`` instead of fake zeros. The delivery-webhook
        scheduler treats ``data_unavailable`` as a soft-skip so we don't
        push misleading "delivering=0" signals to buyers while reporting
        sync hasn't run / scope is pending."""
        from src.adapters.base import DeliveryDataUnavailable

        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=False, tenant_id="t1")

        mock_repo = MagicMock()
        mock_repo.list_by_insertion_order.return_value = []

        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.FreeWheelPlacementStatsRepository",
            lambda session, tenant_id: mock_repo,
        )
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        date_range = ReportingPeriod(start=datetime.now(UTC) - timedelta(days=7), end=datetime.now(UTC))
        with pytest.raises(DeliveryDataUnavailable) as exc_info:
            adapter.get_media_buy_delivery("freewheel_io_777", date_range, datetime.now(UTC))
        assert exc_info.value.media_buy_id == "freewheel_io_777"

    def test_aggregates_rows_into_totals(self, mock_principal, monkeypatch):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=False, tenant_id="t1")

        rows = [
            _make_stats_row(placement_id="p1", impressions=10_000, spend_micros=50_000_000, completed_views=8_000),
            _make_stats_row(placement_id="p2", impressions=20_000, spend_micros=80_000_000, completed_views=16_000),
        ]
        mock_repo = MagicMock()
        mock_repo.list_by_insertion_order.return_value = rows

        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.FreeWheelPlacementStatsRepository",
            lambda session, tenant_id: mock_repo,
        )
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        date_range = ReportingPeriod(start=datetime.now(UTC) - timedelta(days=7), end=datetime.now(UTC))
        resp = adapter.get_media_buy_delivery("freewheel_io_777", date_range, datetime.now(UTC))

        assert resp.totals.impressions == 30_000.0
        assert resp.totals.spend == 130.0  # 50 + 80
        assert resp.totals.completed_views == 24_000.0
        assert resp.totals.completion_rate == pytest.approx(24_000 / 30_000)
        assert resp.currency == "EUR"

        package_ids = {p.package_id for p in resp.by_package}
        assert package_ids == {"p1", "p2"}
