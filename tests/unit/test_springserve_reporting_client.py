"""Tests for the SpringServe Reporting API client.

Covers JobSpec -> request body, sync POST, async submit + poll, and the
ColumnMap-driven row parsing.

Wire format verified live (May 2026): see ``_reporting.py`` module
docstring for the full request/response contract. The ``LIVE_ROW``
fixture is a verbatim row from a real response so the parser is
exercised against the real shape, not a stub.
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

# Verbatim row from a live POST /report response (interval=day, single
# demand_tag_id filter). Trimmed to the columns the parser touches plus
# a few neighbors so the test still validates ignore-extra behavior.
LIVE_ROW = {
    "date": "2026-02-10 00:00:00.0",
    "demand_requests": 8887,
    "bids": 8887,
    "wins": 8887,
    "impressions": 1108,
    "starts": 1108,
    "cost": 35.65928,
    "cpm": 32.18347,
    "clicks": 15,
    "first_quartile": 930,
    "second_quartile": 839,
    "third_quartile": 757,
    "fourth_quartile": 716,
    "click_through_rate": 0.01354,
}


@pytest.fixture
def transport():
    return MagicMock()


@pytest.fixture
def reporting(transport):
    return SpringServeReportingClient(transport)


class TestJobSpec:
    def test_minimal_body_uses_live_field_names(self):
        """Live API uses ``start_date``/``end_date`` (not ``date_start``)."""
        spec = JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14))
        body = spec.to_body()
        assert body["start_date"] == "2026-05-14"
        assert body["end_date"] == "2026-05-14"
        assert body["interval"] == "day"
        # Never send dimensions/metrics -- they silently zero the response.
        assert "dimensions" not in body
        assert "metrics" not in body
        assert "demand_tag_ids" not in body
        assert "async" not in body

    def test_single_demand_tag_id_in_top_level_array(self):
        """Filter goes to top-level ``demand_tag_ids`` (plural), as int."""
        spec = JobSpec(
            start_date=date(2026, 5, 14),
            end_date=date(2026, 5, 14),
            demand_tag_id="2149077",
        )
        body = spec.to_body()
        assert body["demand_tag_ids"] == [2149077]
        # Never send the nested ``filters`` shape -- API silently ignores it.
        assert "filters" not in body

    def test_async_flag_in_body(self):
        spec = JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14), use_async=True)
        assert spec.to_body()["async"] is True

    def test_interval_can_be_dropped(self):
        spec = JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14), interval=None)
        assert "interval" not in spec.to_body()


class TestSyncSubmit:
    def test_submit_sync_parses_live_row_shape(self, reporting, transport):
        """Live response is a top-level array of rows, no envelope."""
        transport.post_json.return_value = [LIVE_ROW]
        spec = JobSpec(start_date=date(2026, 2, 10), end_date=date(2026, 2, 10), demand_tag_id="2149081")

        rows = reporting.submit_sync(spec)

        transport.post_json.assert_called_once_with(
            "/report",
            {
                "start_date": "2026-02-10",
                "end_date": "2026-02-10",
                "interval": "day",
                "demand_tag_ids": [2149081],
            },
        )
        assert len(rows) == 1
        row = rows[0]
        # demand_tag_id is injected from the JobSpec -- API doesn't echo it.
        assert row.demand_tag_id == "2149081"
        assert row.impressions == 1108
        # fourth_quartile is the completes-equivalent column on this API.
        assert row.completed_views == 716
        assert row.clicks == 15
        # 35.65928 EUR -> 35_659_280 micros (rounded).
        assert row.spend_micros == 35_659_280
        assert row.report_date == "2026-02-10 00:00:00.0"

    def test_submit_sync_handles_empty_array(self, reporting, transport):
        transport.post_json.return_value = []
        rows = reporting.submit_sync(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14)))
        assert rows == []

    def test_submit_sync_handles_unexpected_shape(self, reporting, transport):
        """Bad response shape doesn't crash -- log + return zero rows."""
        transport.post_json.return_value = {"unexpected": "shape"}
        rows = reporting.submit_sync(JobSpec(start_date=date(2026, 5, 14), end_date=date(2026, 5, 14)))
        assert rows == []

    def test_submit_sync_tolerates_data_wrapper(self, reporting, transport):
        """Async DONE envelope may wrap rows in ``data`` -- accept both shapes."""
        transport.post_json.return_value = {"data": [LIVE_ROW]}
        spec = JobSpec(start_date=date(2026, 2, 10), end_date=date(2026, 2, 10), demand_tag_id="2149081")
        rows = reporting.submit_sync(spec)
        assert len(rows) == 1
        assert rows[0].impressions == 1108


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
    def test_demand_tag_id_injected_from_caller(self):
        row = parse_row(LIVE_ROW, demand_tag_id="2149081")
        assert row.demand_tag_id == "2149081"
        assert row.impressions == 1108

    def test_null_clicks_preserved(self):
        row = parse_row({"impressions": 100, "clicks": None, "cost": 0}, demand_tag_id="1")
        assert row.clicks is None

    def test_custom_column_map(self):
        """ColumnMap lets a SpringServe schema change get patched without code edits."""
        cm = ColumnMap(impressions="imp", spend="cost_eur")
        row = parse_row({"imp": 999, "cost_eur": 1.50}, demand_tag_id="1", column_map=cm)
        assert row.impressions == 999
        assert row.spend_micros == 1_500_000

    def test_spend_zero_when_missing(self):
        row = parse_row({"impressions": 100}, demand_tag_id="1")
        assert row.spend_micros == 0

    def test_missing_date_yields_none(self):
        row = parse_row({"impressions": 1}, demand_tag_id="1")
        assert row.report_date is None
