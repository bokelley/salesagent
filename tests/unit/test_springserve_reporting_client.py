"""Tests for the SpringServe Reporting API client.

Covers JobSpec → request body, sync POST, async submit + poll, and the
ColumnMap-driven row parsing.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._reporting import (
    ColumnMap,
    JobSpec,
    ReportingError,
    SpringServeReportingClient,
    parse_row,
)


@pytest.fixture
def transport():
    return MagicMock()


@pytest.fixture
def reporting(transport):
    return SpringServeReportingClient(transport)


class TestJobSpec:
    def test_minimal_body_shape(self):
        spec = JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14))
        body = spec.to_body()
        assert body["date_start"] == "2026-05-14"
        assert body["date_end"] == "2026-05-14"
        assert "filters" not in body  # absent when no demand_tag_ids
        assert "async" not in body  # absent when sync

    def test_filters_include_demand_tag_ids_as_ints(self):
        spec = JobSpec(
            start_date=date(2026, 5, 14),
            end_date=date(2026, 5, 14),
            demand_tag_ids=["2149077", "2149080"],
        )
        body = spec.to_body()
        assert body["filters"] == {"demand_tag_id": [2149077, 2149080]}

    def test_async_flag_in_body(self):
        spec = JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14), use_async=True)
        assert spec.to_body()["async"] is True


class TestSyncSubmit:
    def test_submit_sync_parses_rows(self, reporting, transport):
        transport.post_json.return_value = {
            "data": [
                {
                    "demand_tag_id": 2149077,
                    "campaign_id": 120669,
                    "impressions": 12345,
                    "completions": 8000,
                    "clicks": 30,
                    "spend": 27.50,
                    "currency": "EUR",
                },
            ]
        }
        spec = JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14))
        rows = reporting.submit_sync(spec)

        # Sync POST should be made exactly once with our spec body.
        transport.post_json.assert_called_once_with(
            "/report",
            {
                "date_start": "2026-05-14",
                "date_end": "2026-05-14",
                "dimensions": ["campaign_id", "demand_tag_id"],
                "metrics": ["impressions", "spend", "completions", "clicks"],
            },
        )
        assert len(rows) == 1
        assert rows[0].demand_tag_id == "2149077"
        assert rows[0].campaign_id == "120669"
        assert rows[0].impressions == 12345
        assert rows[0].completed_views == 8000
        assert rows[0].clicks == 30
        # Spend converted from EUR major units to micros (27.50 EUR -> 27_500_000)
        assert rows[0].spend_micros == 27_500_000
        assert rows[0].currency == "EUR"

    def test_submit_sync_handles_empty_data(self, reporting, transport):
        transport.post_json.return_value = {"data": []}
        rows = reporting.submit_sync(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14)))
        assert rows == []

    def test_submit_sync_handles_missing_data_wrapper(self, reporting, transport):
        """Bad response shape doesn't crash -- log + return zero rows."""
        transport.post_json.return_value = {"unexpected": "shape"}
        rows = reporting.submit_sync(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14)))
        assert rows == []


class TestAsyncSubmit:
    def test_submit_async_returns_report_id(self, reporting, transport):
        transport.post_json.return_value = {"report_id": "rpt-123", "status": "PENDING"}
        report_id = reporting.submit_async(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 20)))
        assert report_id == "rpt-123"

    def test_submit_async_accepts_id_alias(self, reporting, transport):
        """Some SpringServe envelopes use ``id`` instead of ``report_id``."""
        transport.post_json.return_value = {"id": "rpt-abc"}
        report_id = reporting.submit_async(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 20)))
        assert report_id == "rpt-abc"

    def test_submit_async_missing_id_raises(self, reporting, transport):
        transport.post_json.return_value = {"status": "PENDING"}
        with pytest.raises(ReportingError, match="missing report_id"):
            reporting.submit_async(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 20)))


class TestPollStatus:
    def test_poll_status_returns_status_string(self, reporting, transport):
        transport.get_json.return_value = {"status": "DONE"}
        assert reporting.poll_status("rpt-1") == "DONE"
        transport.get_json.assert_called_once_with("/report/rpt-1")

    def test_poll_until_done_returns_when_terminal_success(self, reporting, transport):
        transport.get_json.side_effect = [
            {"status": "PENDING"},
            {"status": "RUNNING"},
            {"status": "DONE"},
        ]
        # Zero-second interval so the test runs fast
        reporting.poll_until_done("rpt-1", interval_seconds=0, max_attempts=10)
        assert transport.get_json.call_count == 3

    def test_poll_until_done_raises_on_error_status(self, reporting, transport):
        transport.get_json.return_value = {"status": "ERRORED"}
        with pytest.raises(ReportingError, match="ERRORED"):
            reporting.poll_until_done("rpt-1", interval_seconds=0, max_attempts=2)

    def test_poll_until_done_raises_on_timeout(self, reporting, transport):
        transport.get_json.return_value = {"status": "PENDING"}
        with pytest.raises(ReportingError, match="did not complete"):
            reporting.poll_until_done("rpt-1", interval_seconds=0, max_attempts=3)


class TestParseRow:
    def test_string_demand_tag_id_preserved(self):
        row = parse_row({"demand_tag_id": "2149077", "impressions": 100, "spend": 1.0})
        assert row is not None
        assert row.demand_tag_id == "2149077"

    def test_missing_demand_tag_id_returns_none(self):
        assert parse_row({"impressions": 100}) is None

    def test_null_clicks_preserved(self):
        row = parse_row({"demand_tag_id": "1", "impressions": 100, "clicks": None, "spend": 0})
        assert row is not None
        assert row.clicks is None

    def test_custom_column_map(self):
        """ColumnMap lets the day-of-scope wiring fix happen without code edits."""
        cm = ColumnMap(impressions="imp", spend="cost_eur")
        row = parse_row({"demand_tag_id": "1", "imp": 999, "cost_eur": 1.50}, column_map=cm)
        assert row is not None
        assert row.impressions == 999
        assert row.spend_micros == 1_500_000

    def test_spend_zero_when_missing(self):
        row = parse_row({"demand_tag_id": "1", "impressions": 100})
        assert row is not None
        assert row.spend_micros == 0
