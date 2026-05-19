"""SpringServe Reporting API client.

SpringServe's Reporting API at ``POST /api/v0/report`` supports two modes:

1. **Synchronous** -- small queries return rows directly in the response
   body (a top-level JSON array, NO envelope). Rate-limited to 10
   req/min/account, separately from the 240 req/min/account general
   API limit.
2. **Asynchronous** -- pass ``async: true`` in the body; the response is
   ``{"report_id": ..., "status": "PENDING"}``. Poll
   ``GET /api/v0/report/{report_id}`` until status is ``DONE``, then
   fetch result rows.

Wire format verified live against ``console.springserve.com`` (May 2026).

  Request body (sync)::

      {
        "start_date": "2026-02-09",
        "end_date":   "2026-02-12",
        "interval":   "day",                # optional; omit for one rollup row
        "demand_tag_ids": [2149081]         # optional; omit for all in scope
      }

  Response (sync, DONE async)::

      [
        {"date": "...", "impressions": 1108, "cost": 35.65,
         "clicks": 15, "fourth_quartile": 716, ...},
        ...
      ]

Important quirks discovered live:

* The API used to be documented with ``date_start``/``date_end`` and
  ``dimensions``/``metrics``. The live API uses ``start_date``/``end_date``
  and silently returns zero rows if ``dimensions`` is supplied. Send
  only the keys we actually need.
* When filtering by ``demand_tag_ids``, rows do **not** include the
  filter id back. Multi-id filters sum across tags. To populate
  per-tag stats, the caller must issue **one job per tag** -- the
  orchestrator (``reporting_sync.py``) does this.
* The completes-equivalent column is ``fourth_quartile``. There is no
  ``completions`` column. Spend is in column ``cost``. There is no
  ``currency`` column -- currency lives on the demand tag itself.

The ``ColumnMap`` indirection is kept so the day a SpringServe schema
change ships we can tune it without touching the parser.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
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

    Defaults match the live wire format observed against
    ``console.springserve.com`` in May 2026. Tune via constructor args
    if SpringServe ships a schema change.
    """

    impressions: str = "impressions"
    completed_views: str = "fourth_quartile"
    clicks: str = "clicks"
    spend: str = "cost"  # currency-major units (e.g. EUR 35.65 -> 35_650_000 micros)


DEFAULT_COLUMN_MAP = ColumnMap()


@dataclass
class ReportRow:
    """One parsed row of the SpringServe Reporting API response.

    ``demand_tag_id`` is injected from the JobSpec context -- the API
    does not echo the filter id back in rows. ``report_date`` is set
    only for interval-bucketed runs.
    """

    demand_tag_id: str
    impressions: int
    completed_views: int | None
    clicks: int | None
    spend_micros: int
    report_date: str | None = None


@dataclass
class JobSpec:
    """Inputs for a single Reporting API call.

    Set ``demand_tag_id`` to scope the report to one tag (the only way
    to get per-tag stats -- multi-id filters sum across tags and the
    rows don't carry the id back). Leave it ``None`` for an unfiltered
    rollup across the account.
    """

    start_date: date
    end_date: date
    demand_tag_id: str | None = None
    interval: str | None = "day"
    use_async: bool = False

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }
        if self.interval:
            body["interval"] = self.interval
        if self.demand_tag_id is not None:
            body["demand_tag_ids"] = [int(self.demand_tag_id)]
        if self.use_async:
            body["async"] = True
        return body


def parse_row(
    row: dict[str, Any],
    *,
    demand_tag_id: str,
    column_map: ColumnMap = DEFAULT_COLUMN_MAP,
) -> ReportRow:
    """Parse one report-row dict into a typed :class:`ReportRow`.

    Spend is converted from currency-major units (SpringServe's wire
    format) into micros for our cache. The demand-tag id is injected
    by the caller because the API doesn't include it in rows.
    """
    impressions = int(row.get(column_map.impressions, 0) or 0)
    completed = row.get(column_map.completed_views)
    completed_views = int(completed) if completed is not None else None
    clicks_val = row.get(column_map.clicks)
    clicks = int(clicks_val) if clicks_val is not None else None
    spend_value = row.get(column_map.spend, 0) or 0
    spend_micros = int(round(float(spend_value) * 1_000_000))
    report_date = row.get("date")
    return ReportRow(
        demand_tag_id=demand_tag_id,
        impressions=impressions,
        completed_views=completed_views,
        clicks=clicks,
        spend_micros=spend_micros,
        report_date=str(report_date) if report_date is not None else None,
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
        return _parse_rows(body, demand_tag_id=spec.demand_tag_id)

    # ----- async -----

    def submit_async(self, spec: JobSpec) -> str:
        """POST /report with ``async: true`` and return the report id."""
        spec.use_async = True
        body = self._transport.post_json("/report", spec.to_body())
        if not isinstance(body, dict):
            raise ReportingError(f"SpringServe async /report response not a dict: {body!r}")
        report_id = body.get("report_id") or body.get("id")
        if not report_id:
            raise ReportingError(f"SpringServe async /report response missing report_id: {body!r}")
        return str(report_id)

    def poll_status(self, report_id: str) -> str:
        """GET /report/{id} and return the ``status`` value."""
        body = self._transport.get_json(f"/report/{report_id}")
        if not isinstance(body, dict):
            return "UNKNOWN"
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

    def fetch_rows(self, report_id: str, *, demand_tag_id: str | None = None) -> list[ReportRow]:
        """GET /report/{id} after status=DONE and return parsed rows."""
        body = self._transport.get_json(f"/report/{report_id}")
        return _parse_rows(body, demand_tag_id=demand_tag_id)


def _parse_rows(body: Any, *, demand_tag_id: str | None) -> list[ReportRow]:
    """Pull rows out of a SpringServe report response.

    The live API returns the row array at the top level. The async
    completion envelope is undocumented; we tolerate either a bare list
    or a ``{"data": [...]}`` / ``{"rows": [...]}`` wrapper for forward
    compatibility.

    When ``demand_tag_id`` is None the rows are unfiltered (rollup across
    the account); they're still parsed so callers can use totals, but
    ``ReportRow.demand_tag_id`` will be the empty string -- callers
    populating per-tag caches MUST pass a non-None ``demand_tag_id``.
    """
    rows = _extract_rows(body)
    parsed: list[ReportRow] = []
    tag_id = demand_tag_id or ""
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        parsed.append(parse_row(raw, demand_tag_id=tag_id))
    return parsed


def _extract_rows(body: Any) -> list[Any]:
    """Return the row list from a SpringServe report response, or [] if shape is wrong."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        rows = body.get("data") or body.get("rows") or []
        if isinstance(rows, list):
            return rows
        logger.warning("SpringServe report 'data'/'rows' not a list: %r", type(rows))
        return []
    logger.warning("SpringServe report response not a list or dict: %r", type(body))
    return []


__all__ = [
    "ColumnMap",
    "DEFAULT_COLUMN_MAP",
    "JobSpec",
    "ReportRow",
    "ReportingError",
    "SpringServeReportingClient",
    "SpringServeForbiddenError",
    "SpringServeServerError",
    "parse_row",
]
