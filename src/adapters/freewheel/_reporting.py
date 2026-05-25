"""FreeWheel Query Reporting API client.

The Reporting API lives at ``api.freewheel.tv/reporting/*`` (singular, host
root — NOT under ``/services/v*``). Confirmed via live probing 2026-05-13:
every ``/reporting/*`` path is a real AWS API Gateway resource that
currently returns IAM-deny for our test user, awaiting a scope grant.

This module is **speculative-but-defensive**: until we have scope to call
the live API, the exact request/response shapes are educated guesses
based on FW's documented patterns and common async-job semantics
(submit → poll → fetch).

Field names that come back from the live API are likely to differ from
what we guess here (e.g. ``Placement ID`` vs ``placement_id`` vs ``id``).
:class:`ColumnMap` is the single place to retune those mappings without
touching the orchestration code. When the first live response comes
back, we update the constants in :data:`DEFAULT_COLUMN_MAP` and the
rest of the stack (cache repository, snapshot reader, delivery
aggregator) keeps working.

What we lock in based on FW's other v4 conventions:
- JSON request/response bodies (their v4 surface is JSON-first).
- ``status`` field on the job uses upper-case ``PENDING`` / ``RUNNING`` /
  ``COMPLETED`` / ``FAILED`` enum-strings — matches their v3 ``status``
  conventions for placements + IOs (we cite this in tests so a future
  status-string mismatch is loud at the unit-test layer, not at runtime
  against a real account).
- Job IDs are strings — modern FW APIs return uuid-ish identifiers.

What we deliberately keep flexible:
- Output column naming (``ColumnMap``).
- Number type — FW might return spend as float, int, or string. The
  result parser coerces.
- Result location — some FW endpoints return rows inline; others stash
  them at a presigned URL. The client supports both.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.adapters.freewheel._transport import FreeWheelTransport

logger = logging.getLogger(__name__)


_BASE = "/reporting"


class JobStatus(str, Enum):
    """Async job lifecycle. Values mirror FW's v3/v4 status conventions
    (upper-case enum-strings). Unknown server values clamp to ``unknown``
    rather than raising — so a fresh enum value FW adds doesn't break
    our polling loop."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | None) -> JobStatus:
        if not value:
            return cls.UNKNOWN
        try:
            return cls(value.upper())
        except ValueError:
            return cls.UNKNOWN


class JobSpec(BaseModel):
    """Specification for a Query Reporting job.

    Translates to the POST /reporting/jobs request body. Field names
    chosen to match FW's documented patterns; the actual wire format may
    need adjustment once we see a real schema (see :data:`DEFAULT_COLUMN_MAP`
    for output-side flexibility).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Caller-supplied job label for tracing")
    dimensions: list[str] = Field(..., min_length=1, description="Group-by fields (e.g. ['placement_id', 'day'])")
    metrics: list[str] = Field(
        ..., min_length=1, description="Aggregated measures (e.g. ['impressions', 'ad_revenue'])"
    )
    start_date: date
    end_date: date
    advertiser_ids: list[str] | None = Field(default=None, description="Optional advertiser_id filter")
    placement_ids: list[str] | None = Field(default=None, description="Optional placement_id filter")
    output_format: str = Field(default="json", description="json | csv")

    def to_request_body(self) -> dict[str, Any]:
        """Serialize to the JSON body expected by POST /reporting/jobs.

        Likely shape — verified against the live API once scope arrives.
        """
        body: dict[str, Any] = {
            "name": self.name,
            "dimensions": list(self.dimensions),
            "metrics": list(self.metrics),
            "filters": {
                "date_range": {
                    "start": self.start_date.isoformat(),
                    "end": self.end_date.isoformat(),
                },
            },
            "output_format": self.output_format,
        }
        if self.advertiser_ids:
            body["filters"]["advertiser_ids"] = list(self.advertiser_ids)
        if self.placement_ids:
            body["filters"]["placement_ids"] = list(self.placement_ids)
        return body


@dataclass
class JobState:
    """Snapshot of a submitted job's lifecycle state."""

    job_id: str
    status: JobStatus
    row_count: int | None = None
    result_url: str | None = None
    error_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, payload: dict[str, Any]) -> JobState:
        """Parse a /reporting/jobs/{id} payload defensively. Field names
        FW returns are best-effort guesses; the ``raw`` dict preserves
        the original payload so callers can recover unknown fields
        without re-fetching."""
        job_id = payload.get("job_id") or payload.get("id") or payload.get("jobId") or ""
        return cls(
            job_id=str(job_id),
            status=JobStatus.parse(payload.get("status") or payload.get("state")),
            row_count=payload.get("row_count") or payload.get("rowCount"),
            result_url=payload.get("result_url") or payload.get("resultUrl") or payload.get("download_url"),
            error_message=payload.get("error_message") or payload.get("error"),
            raw=payload,
        )


@dataclass
class ColumnMap:
    """Maps FW result-column names → our cache schema field names.

    Day-of-scope: when we see the first real result row, update the
    fields below to whatever FW actually returns (e.g. ``"impressions"``
    might be ``"Impressions"`` or ``"imp"`` — set it once, the rest of
    the stack keeps working).
    """

    placement_id: str = "placement_id"
    insertion_order_id: str = "insertion_order_id"
    impressions: str = "impressions"
    completed_views: str = "completed_views"
    clicks: str = "clicks"
    spend: str = "ad_revenue"  # FW commonly labels publisher-side spend "ad_revenue"
    currency: str = "currency"
    as_of: str = "as_of"


DEFAULT_COLUMN_MAP = ColumnMap()


def parse_row(row: dict[str, Any], column_map: ColumnMap = DEFAULT_COLUMN_MAP) -> dict[str, Any]:
    """Coerce one FW result row into our placement-stats cache shape.

    Returns a dict shaped for
    :meth:`FreeWheelPlacementStatsRepository.bulk_upsert` — pre-converted
    to spend_micros (1 EUR = 1_000_000) so caller doesn't have to know
    about that. Missing fields surface as ``None`` rather than raising —
    a column FW unexpectedly omits shouldn't kill the whole sync.
    """

    def _int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _spend_to_micros(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(round(float(value) * 1_000_000))
        except (TypeError, ValueError):
            return None

    return {
        "placement_id": str(row.get(column_map.placement_id, "")) or None,
        "insertion_order_id": (
            str(row[column_map.insertion_order_id])
            if column_map.insertion_order_id in row and row[column_map.insertion_order_id]
            else None
        ),
        "impressions": _int(row.get(column_map.impressions)) or 0,
        "completed_views": _int(row.get(column_map.completed_views)),
        "clicks": _int(row.get(column_map.clicks)),
        "spend_micros": _spend_to_micros(row.get(column_map.spend)) or 0,
        "currency": (row.get(column_map.currency) or None),
        "as_of": _parse_as_of(row.get(column_map.as_of)),
    }


def _parse_as_of(value: Any) -> datetime:
    """Coerce the row's data-freshness timestamp; default to now() if absent."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    from datetime import UTC

    return datetime.now(UTC)


class FreeWheelReportingClient:
    """Thin HTTP client over the /reporting/* surface.

    Construction takes a :class:`FreeWheelTransport` so the client gets
    the same auth refresh + retry behaviour as the rest of the FW
    sub-clients. The orchestration (submit → poll → fetch → parse) lives
    in :class:`FreeWheelReportingSync`; this class is intentionally
    keyed to one job at a time.
    """

    def __init__(self, transport: FreeWheelTransport) -> None:
        self._transport = transport

    def submit_job(self, spec: JobSpec) -> JobState:
        """POST /reporting/jobs with the spec; return the initial JobState."""
        payload = self._transport.post_json(f"{_BASE}/jobs", spec.to_request_body())
        return JobState.from_api_response(payload)

    def get_job(self, job_id: str) -> JobState:
        """GET /reporting/jobs/{id}; return current JobState."""
        payload = self._transport.get_json(f"{_BASE}/jobs/{job_id}")
        return JobState.from_api_response(payload)

    def fetch_results(self, state: JobState) -> list[dict[str, Any]]:
        """Pull result rows for a completed job.

        Tries the inline path first (``GET /reporting/jobs/{id}/results``)
        and falls back to ``state.result_url`` if FW points us at a
        presigned URL. Both shapes appear in FW docs for different
        report kinds.
        """
        if state.status is not JobStatus.COMPLETED:
            raise ReportingJobNotComplete(state)

        # Inline results path
        try:
            payload = self._transport.get_json(f"{_BASE}/jobs/{state.job_id}/results")
            rows = payload.get("rows") or payload.get("results") or payload.get("data")
            if isinstance(rows, list):
                return rows
        except FreeWheelNotFoundError:
            pass  # try the presigned URL path

        # Presigned URL path
        if state.result_url:
            import requests

            response = requests.get(state.result_url, timeout=30)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as e:
                # CSV fallback — not implemented yet, kept TODO for day-of-scope
                raise ReportingResultFormatUnsupported(
                    "Result body was not JSON; CSV/Parquet parsing pending live verification."
                ) from e
            rows = payload.get("rows") or payload.get("results") or payload.get("data") or payload
            if isinstance(rows, list):
                return rows

        raise ReportingResultNotFound(state)

    def wait_for_completion(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 5.0,
    ) -> JobState:
        """Poll /reporting/jobs/{id} until terminal state. Backoff is
        linear (FW's reporting jobs typically complete in 10-30s; longer
        intervals waste latency)."""
        deadline = time.monotonic() + timeout_seconds
        last: JobState | None = None
        while time.monotonic() < deadline:
            state = self.get_job(job_id)
            last = state
            if state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED):
                return state
            time.sleep(poll_interval_seconds)
        raise ReportingJobTimeout(last or JobState(job_id=job_id, status=JobStatus.UNKNOWN), timeout_seconds)


class ReportingError(RuntimeError):
    """Base class for Reporting-client errors."""


class ReportingJobNotComplete(ReportingError):
    """Caller tried to fetch results for a job that hasn't completed."""

    def __init__(self, state: JobState) -> None:
        super().__init__(f"Job {state.job_id} status is {state.status.value} — cannot fetch results yet.")
        self.state = state


class ReportingResultNotFound(ReportingError):
    """Job completed but we couldn't locate result rows."""

    def __init__(self, state: JobState) -> None:
        super().__init__(f"Job {state.job_id} completed but neither inline results nor result_url yielded any rows.")
        self.state = state


class ReportingResultFormatUnsupported(ReportingError):
    """Result body was not JSON; we haven't implemented CSV/Parquet yet."""


class ReportingJobTimeout(ReportingError):
    """The job didn't reach a terminal state within ``timeout_seconds``."""

    def __init__(self, state: JobState, timeout_seconds: float) -> None:
        super().__init__(
            f"Job {state.job_id} did not finish within {timeout_seconds}s (last status: {state.status.value})."
        )
        self.state = state
        self.timeout_seconds = timeout_seconds


# Late import to avoid a circular at module load — FreeWheelNotFoundError
# lives in _transport.py which already imports requests.
from src.adapters.freewheel._transport import FreeWheelNotFoundError  # noqa: E402
