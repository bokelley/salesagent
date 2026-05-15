"""SpringServe Reporting API client.

SpringServe's Reporting API at ``POST /api/v0/report`` supports two modes:

1. **Synchronous** -- small queries return rows directly in the response
   body. Rate-limited to 10 req/min/account, separately from the 240
   req/min/account general API limit.
2. **Asynchronous** -- pass ``async: true`` in the body; the response is
   ``{"report_id": ..., "status": "PENDING"}``. Poll
   ``GET /api/v0/report/{report_id}`` until status is ``DONE``, then
   fetch result rows.

Today's report shape (built from docs; the live wire format gets
re-verified the first time SpringServe grants reporting scope on the
operator's account):

  Request body::

      {
        "date_start": "2026-05-14",
        "date_end": "2026-05-14",
        "dimensions": ["campaign_id", "demand_tag_id"],
        "metrics": ["impressions", "spend", "completions", "clicks"],
        "filters": {"demand_tag_id": [...]}
      }

  Sync response: ``{"data": [{...row...}, ...]}``  (column-keyed)
  Async response: ``{"report_id": ..., "status": "PENDING"}``

The ``ColumnMap`` lets the Stage-4 day-of-scope fix happen by tweaking
column names instead of code, the same way the FreeWheel adapter does.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.adapters.springserve._transport import (
    SpringServeForbiddenError,
    SpringServeServerError,
    SpringServeTransport,
)

logger = logging.getLogger(__name__)


class ReportingError(RuntimeError):
    """Reporting API call succeeded HTTP-wise but the report payload
    indicates failure (job ERRORED, missing columns, etc.)."""


@dataclass
class ColumnMap:
    """Map SpringServe report-row column names to the SpringServeDemandTagStats fields.

    SpringServe's report schema is not 100% documented; tune these names
    once we see real responses. Defaults match the field names from the
    Reporting API documentation as of 2026-05.
    """

    demand_tag_id: str = "demand_tag_id"
    campaign_id: str = "campaign_id"
    impressions: str = "impressions"
    completed_views: str = "completions"
    clicks: str = "clicks"
    spend: str = "spend"  # already in currency-major units
    currency: str = "currency"


DEFAULT_COLUMN_MAP = ColumnMap()

DEFAULT_DIMENSIONS = ["campaign_id", "demand_tag_id"]
DEFAULT_METRICS = ["impressions", "spend", "completions", "clicks"]


@dataclass
class ReportRow:
    """One parsed row of the SpringServe Reporting API response."""

    demand_tag_id: str
    campaign_id: str | None
    impressions: int
    completed_views: int | None
    clicks: int | None
    spend_micros: int
    currency: str | None


@dataclass
class JobSpec:
    """Inputs for a single Reporting API call."""

    start_date: date
    end_date: date
    demand_tag_ids: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=lambda: list(DEFAULT_DIMENSIONS))
    metrics: list[str] = field(default_factory=lambda: list(DEFAULT_METRICS))
    use_async: bool = False

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "date_start": self.start_date.isoformat(),
            "date_end": self.end_date.isoformat(),
            "dimensions": list(self.dimensions),
            "metrics": list(self.metrics),
        }
        if self.demand_tag_ids:
            body["filters"] = {"demand_tag_id": [int(x) for x in self.demand_tag_ids]}
        if self.use_async:
            body["async"] = True
        return body


def parse_row(row: dict[str, Any], column_map: ColumnMap = DEFAULT_COLUMN_MAP) -> ReportRow | None:
    """Parse one report-row dict into a typed :class:`ReportRow`.

    Returns ``None`` when the row is unusable (missing the demand-tag id).
    Spend is converted from currency-major units (the SpringServe wire
    format) into micros for our cache.
    """
    demand_tag_id = row.get(column_map.demand_tag_id)
    if demand_tag_id is None:
        return None
    impressions = int(row.get(column_map.impressions, 0) or 0)
    completed = row.get(column_map.completed_views)
    completed_views = int(completed) if completed is not None else None
    clicks_val = row.get(column_map.clicks)
    clicks = int(clicks_val) if clicks_val is not None else None
    spend_value = row.get(column_map.spend, 0) or 0
    spend_micros = int(round(float(spend_value) * 1_000_000))
    return ReportRow(
        demand_tag_id=str(demand_tag_id),
        campaign_id=str(row[column_map.campaign_id]) if row.get(column_map.campaign_id) is not None else None,
        impressions=impressions,
        completed_views=completed_views,
        clicks=clicks,
        spend_micros=spend_micros,
        currency=row.get(column_map.currency) or None,
    )


class SpringServeReportingClient:
    """Submit + poll Reporting API jobs against ``/report``.

    For small windows use ``submit_sync(...)`` -- one round-trip returns
    parsed rows. For larger windows use ``submit_async(...)`` +
    ``poll_until_done(report_id)`` + ``fetch_rows(report_id)``.
    """

    def __init__(self, transport: SpringServeTransport):
        self._transport = transport

    # ----- sync -----

    def submit_sync(self, spec: JobSpec) -> list[ReportRow]:
        """POST /report synchronously. Returns parsed rows directly."""
        spec.use_async = False
        body = self._transport.post_json("/report", spec.to_body())
        return _parse_rows(body)

    # ----- async -----

    def submit_async(self, spec: JobSpec) -> str:
        """POST /report with ``async: true`` and return the report id."""
        spec.use_async = True
        body = self._transport.post_json("/report", spec.to_body())
        report_id = body.get("report_id") or body.get("id")
        if not report_id:
            raise ReportingError(f"SpringServe async /report response missing report_id: {body!r}")
        return str(report_id)

    def poll_status(self, report_id: str) -> str:
        """GET /report/{id} and return the ``status`` value."""
        body = self._transport.get_json(f"/report/{report_id}")
        return str(body.get("status", "UNKNOWN"))

    def poll_until_done(
        self,
        report_id: str,
        *,
        interval_seconds: float = 5.0,
        max_attempts: int = 60,
    ) -> None:
        """Poll until status is DONE or terminal error.

        Default 60 attempts at 5s = 5 minutes total. Tune per-job from
        the calling sync layer.
        """
        for _ in range(max_attempts):
            status = self.poll_status(report_id)
            if status in {"DONE", "COMPLETED", "SUCCESS"}:
                return
            if status in {"ERRORED", "FAILED", "CANCELLED"}:
                raise ReportingError(f"SpringServe report {report_id} ended in status {status!r}")
            time.sleep(interval_seconds)
        raise ReportingError(f"SpringServe report {report_id} did not complete after {max_attempts} polls")

    def fetch_rows(self, report_id: str) -> list[ReportRow]:
        """GET /report/{id} after status=DONE and return parsed rows."""
        body = self._transport.get_json(f"/report/{report_id}")
        return _parse_rows(body)


def _parse_rows(body: Any) -> list[ReportRow]:
    """Pull rows out of a SpringServe report response envelope.

    SpringServe wraps result rows under ``data`` for sync and (per the
    docs) the same way once a job is DONE. Anything missing the wrapper
    is logged + treated as zero rows so a misformatted response doesn't
    crash the sync.
    """
    if not isinstance(body, dict):
        logger.warning("SpringServe report response not a dict: %r", type(body))
        return []
    rows = body.get("data") or body.get("rows") or []
    if not isinstance(rows, list):
        logger.warning("SpringServe report 'data' not a list: %r", type(rows))
        return []
    parsed: list[ReportRow] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = parse_row(raw)
        if row is not None:
            parsed.append(row)
    return parsed


__all__ = [
    "ColumnMap",
    "DEFAULT_COLUMN_MAP",
    "DEFAULT_DIMENSIONS",
    "DEFAULT_METRICS",
    "JobSpec",
    "ReportRow",
    "ReportingError",
    "SpringServeReportingClient",
    "SpringServeForbiddenError",
    "SpringServeServerError",
    "parse_row",
]
