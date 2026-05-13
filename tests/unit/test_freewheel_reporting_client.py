"""Tests for the FreeWheel Query Reporting API client.

The Reporting API surface is speculative (we lack scope to call it live),
so these tests pin the client's CONTRACT: how it serialises specs, parses
responses, handles polling, and degrades on partial / unexpected payloads.
When the first real response from FW comes back, the column-name fix lives
in :data:`DEFAULT_COLUMN_MAP` — the surrounding orchestration shouldn't
need to change.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from src.adapters.freewheel._reporting import (
    ColumnMap,
    FreeWheelReportingClient,
    JobSpec,
    JobState,
    JobStatus,
    ReportingJobNotComplete,
    ReportingJobTimeout,
    parse_row,
)

# ---------------------------------------------------------------------------
# JobSpec / JobStatus / JobState — pure model surface
# ---------------------------------------------------------------------------


class TestJobSpec:
    def test_serialises_minimum_request_body(self):
        spec = JobSpec(
            name="t",
            dimensions=["placement_id"],
            metrics=["impressions"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 13),
        )
        body = spec.to_request_body()
        assert body["name"] == "t"
        assert body["dimensions"] == ["placement_id"]
        assert body["metrics"] == ["impressions"]
        assert body["filters"]["date_range"] == {"start": "2026-05-01", "end": "2026-05-13"}
        assert body["output_format"] == "json"
        # No advertiser/placement filters when caller didn't pass them
        assert "advertiser_ids" not in body["filters"]
        assert "placement_ids" not in body["filters"]

    def test_serialises_with_advertiser_and_placement_filters(self):
        spec = JobSpec(
            name="t",
            dimensions=["placement_id"],
            metrics=["impressions"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            advertiser_ids=["1356511"],
            placement_ids=["90997225", "90997226"],
        )
        body = spec.to_request_body()
        assert body["filters"]["advertiser_ids"] == ["1356511"]
        assert body["filters"]["placement_ids"] == ["90997225", "90997226"]


class TestJobStatusParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("PENDING", JobStatus.PENDING),
            ("running", JobStatus.RUNNING),  # case-insensitive
            ("Completed", JobStatus.COMPLETED),
            ("FAILED", JobStatus.FAILED),
            ("CANCELED", JobStatus.CANCELED),
        ],
    )
    def test_known_statuses(self, raw, expected):
        assert JobStatus.parse(raw) == expected

    def test_unknown_value_returns_unknown(self):
        """A status FW adds later (e.g. 'QUEUED') must not crash the
        polling loop — clamp to UNKNOWN and keep waiting."""
        assert JobStatus.parse("QUEUED") == JobStatus.UNKNOWN

    def test_none_returns_unknown(self):
        assert JobStatus.parse(None) == JobStatus.UNKNOWN


class TestJobStateParsing:
    def test_parses_common_payload_shape(self):
        state = JobState.from_api_response(
            {
                "job_id": "abc123",
                "status": "RUNNING",
                "row_count": 100,
                "result_url": "https://signed.example/foo",
            }
        )
        assert state.job_id == "abc123"
        assert state.status == JobStatus.RUNNING
        assert state.row_count == 100
        assert state.result_url == "https://signed.example/foo"
        assert state.error_message is None

    def test_parses_camelCase_payload_variant(self):
        """FW v4 payloads often arrive camelCase; both shapes must work."""
        state = JobState.from_api_response({"id": "x", "state": "completed", "rowCount": 50, "resultUrl": "u"})
        assert state.job_id == "x"
        assert state.status == JobStatus.COMPLETED
        assert state.row_count == 50
        assert state.result_url == "u"

    def test_preserves_raw_payload_for_unknown_fields(self):
        """When FW adds fields we don't yet parse, callers can still recover
        them from ``state.raw`` without re-fetching."""
        payload = {"job_id": "x", "status": "COMPLETED", "unexpected_field": 42}
        state = JobState.from_api_response(payload)
        assert state.raw["unexpected_field"] == 42

    def test_failed_job_carries_error_message(self):
        state = JobState.from_api_response({"job_id": "x", "status": "FAILED", "error_message": "query timeout"})
        assert state.status == JobStatus.FAILED
        assert state.error_message == "query timeout"


# ---------------------------------------------------------------------------
# parse_row — column-map defensive parsing
# ---------------------------------------------------------------------------


class TestParseRow:
    def test_default_column_map_extracts_expected_fields(self):
        row = {
            "placement_id": "90997225",
            "insertion_order_id": "90763088",
            "impressions": 10_000,
            "completed_views": 8_500,
            "clicks": 12,
            "ad_revenue": 50.0,
            "currency": "EUR",
            "as_of": "2026-05-13T20:00:00Z",
        }
        parsed = parse_row(row)
        assert parsed["placement_id"] == "90997225"
        assert parsed["insertion_order_id"] == "90763088"
        assert parsed["impressions"] == 10_000
        assert parsed["completed_views"] == 8_500
        assert parsed["clicks"] == 12
        assert parsed["spend_micros"] == 50_000_000  # 50.0 EUR → 50M micros
        assert parsed["currency"] == "EUR"
        assert isinstance(parsed["as_of"], datetime)

    def test_handles_string_numbers(self):
        """FW may return numbers as JSON strings ("10000") — coerce."""
        row = {"placement_id": "x", "impressions": "10000", "ad_revenue": "50.0"}
        parsed = parse_row(row)
        assert parsed["impressions"] == 10_000
        assert parsed["spend_micros"] == 50_000_000

    def test_missing_numeric_fields_become_zero_or_none(self):
        """A column FW unexpectedly omits shouldn't kill the sync; spend +
        impressions default to 0 (the cache columns are NOT NULL), while
        optional metrics default to None."""
        parsed = parse_row({"placement_id": "x"})
        assert parsed["impressions"] == 0
        assert parsed["spend_micros"] == 0
        assert parsed["completed_views"] is None
        assert parsed["clicks"] is None

    def test_garbage_numeric_input_falls_through_to_default(self):
        """If FW returns 'NaN' or some string we can't parse, we don't crash."""
        parsed = parse_row({"placement_id": "x", "impressions": "junk", "ad_revenue": "junk"})
        assert parsed["impressions"] == 0
        assert parsed["spend_micros"] == 0

    def test_custom_column_map_remaps_field_names(self):
        """Day-of-scope tunable: when FW returns 'Placement ID' instead of
        'placement_id', we update the ColumnMap, not the orchestration."""
        custom = ColumnMap(
            placement_id="Placement ID",
            impressions="Impressions",
            spend="Revenue",
        )
        row = {"Placement ID": "x", "Impressions": "100", "Revenue": "1.50"}
        parsed = parse_row(row, custom)
        assert parsed["placement_id"] == "x"
        assert parsed["impressions"] == 100
        assert parsed["spend_micros"] == 1_500_000

    def test_as_of_falls_back_to_now_when_missing(self):
        parsed = parse_row({"placement_id": "x"})
        assert parsed["as_of"].tzinfo is not None  # tz-aware
        # Sanity: within the last minute
        delta = (datetime.now(UTC) - parsed["as_of"]).total_seconds()
        assert -1.0 < delta < 60.0


# ---------------------------------------------------------------------------
# FreeWheelReportingClient — orchestration with mocked transport
# ---------------------------------------------------------------------------


class TestSubmitJob:
    def test_submit_round_trips_through_transport(self):
        transport = MagicMock()
        transport.post_json.return_value = {"job_id": "abc", "status": "PENDING"}
        client = FreeWheelReportingClient(transport)
        spec = JobSpec(
            name="t",
            dimensions=["placement_id"],
            metrics=["impressions"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
        )

        state = client.submit_job(spec)

        assert state.job_id == "abc"
        assert state.status == JobStatus.PENDING
        # Verify the wire-level call
        transport.post_json.assert_called_once_with(
            "/reporting/jobs",
            spec.to_request_body(),
        )


class TestWaitForCompletion:
    def test_returns_immediately_when_already_terminal(self):
        transport = MagicMock()
        transport.get_json.return_value = {"job_id": "x", "status": "COMPLETED", "row_count": 5}
        client = FreeWheelReportingClient(transport)
        state = client.wait_for_completion("x", timeout_seconds=10, poll_interval_seconds=0.01)
        assert state.status == JobStatus.COMPLETED
        # one GET — no extra polls
        assert transport.get_json.call_count == 1

    def test_polls_until_complete(self):
        """The polling loop walks PENDING → RUNNING → COMPLETED."""
        transport = MagicMock()
        transport.get_json.side_effect = [
            {"job_id": "x", "status": "PENDING"},
            {"job_id": "x", "status": "RUNNING"},
            {"job_id": "x", "status": "COMPLETED", "row_count": 3},
        ]
        client = FreeWheelReportingClient(transport)
        state = client.wait_for_completion("x", timeout_seconds=10, poll_interval_seconds=0.01)
        assert state.status == JobStatus.COMPLETED
        assert transport.get_json.call_count == 3

    def test_timeout_raises_with_last_state(self):
        """If FW never returns a terminal status we raise — and the timeout
        carries the last-seen state so callers can log it."""
        transport = MagicMock()
        transport.get_json.return_value = {"job_id": "x", "status": "RUNNING"}
        client = FreeWheelReportingClient(transport)
        with pytest.raises(ReportingJobTimeout) as exc_info:
            client.wait_for_completion("x", timeout_seconds=0.05, poll_interval_seconds=0.01)
        assert exc_info.value.state.status == JobStatus.RUNNING

    def test_canceled_status_is_terminal(self):
        """If FW cancels the job (rate-limit, ops action), we stop polling
        rather than spin until timeout."""
        transport = MagicMock()
        transport.get_json.return_value = {"job_id": "x", "status": "CANCELED"}
        client = FreeWheelReportingClient(transport)
        state = client.wait_for_completion("x", timeout_seconds=10, poll_interval_seconds=0.01)
        assert state.status == JobStatus.CANCELED


class TestFetchResults:
    def test_returns_inline_rows_when_present(self):
        transport = MagicMock()
        transport.get_json.return_value = {"rows": [{"placement_id": "x", "impressions": 10}]}
        client = FreeWheelReportingClient(transport)
        state = JobState(job_id="x", status=JobStatus.COMPLETED)
        rows = client.fetch_results(state)
        assert rows == [{"placement_id": "x", "impressions": 10}]
        transport.get_json.assert_called_once_with("/reporting/jobs/x/results")

    def test_accepts_alternate_result_payload_keys(self):
        """FW may key the result rows as 'rows' or 'results' or 'data'."""
        transport = MagicMock()
        transport.get_json.return_value = {"data": [{"placement_id": "y"}]}
        client = FreeWheelReportingClient(transport)
        state = JobState(job_id="x", status=JobStatus.COMPLETED)
        assert client.fetch_results(state) == [{"placement_id": "y"}]

    def test_raises_when_job_not_complete(self):
        """Calling fetch_results before the job finishes is a caller bug;
        surface it as a clear error rather than firing a doomed GET."""
        transport = MagicMock()
        client = FreeWheelReportingClient(transport)
        state = JobState(job_id="x", status=JobStatus.RUNNING)
        with pytest.raises(ReportingJobNotComplete):
            client.fetch_results(state)
        transport.get_json.assert_not_called()
