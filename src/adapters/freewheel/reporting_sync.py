"""FreeWheel Query Reporting API sync — orchestrator.

FW's Reporting API lives at ``/reporting/*`` (singular) at the host root,
NOT under ``/services/v*``. Verified live: every variant returns AWS API
Gateway IAM-deny for our test user, confirming the resources exist and
just need a scope grant.

Surface map (probed 2026-05-13):
    POST /reporting/jobs                         — submit async report
    GET  /reporting/jobs                         — list jobs
    GET  /reporting/jobs/{id}                    — poll status
    GET  /reporting/jobs/{id}/result(s)/download — fetch CSV/JSON output
    GET  /reporting/queries                      — saved queries (CRUD)
    GET  /reporting/saved_queries                — same family
    GET  /reporting/dimensions                   — list available report dimensions
    GET  /reporting/metrics                      — list available metrics
    GET  /reporting/fields, /schema              — full schema introspection

Orchestration:
    1. Build a :class:`JobSpec` covering the requested placements (or
       all in the tenant if ``placement_ids`` is None).
    2. POST /reporting/jobs → JobState{status=PENDING}.
    3. Poll /reporting/jobs/{id} until terminal.
    4. Fetch result rows.
    5. Coerce each row via :func:`parse_row` (configurable ColumnMap).
    6. Bulk-upsert into ``freewheel_placement_stats`` via
       :class:`FreeWheelPlacementStatsRepository`.

When scope isn't granted yet the underlying HTTP call surfaces a
:class:`FreeWheelForbiddenError`; we trap it once at the top of
:meth:`run` and raise :class:`ReportingScopeNotGranted` so schedulers
get a clean signal rather than a raw 403.

Today (pre-scope): calling :meth:`run` raises immediately because the
first /reporting/jobs POST returns 403. The read paths
(``get_packages_snapshot``, ``get_media_buy_delivery``) already tolerate
an empty cache, so nothing breaks.

Day-of-scope: verify request/response shapes match what FW actually
returns. The only likely fix is updating :data:`DEFAULT_COLUMN_MAP`
in ``_reporting.py`` to match FW's real column names (Placement ID vs
placement_id vs id, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from src.adapters.freewheel._reporting import (
    DEFAULT_COLUMN_MAP,
    ColumnMap,
    FreeWheelReportingClient,
    JobSpec,
    JobStatus,
    ReportingError,
    parse_row,
)
from src.adapters.freewheel._transport import FreeWheelForbiddenError
from src.adapters.freewheel.client import FreeWheelClient
from src.core.database.repositories.freewheel_placement_stats import FreeWheelPlacementStatsRepository

logger = logging.getLogger(__name__)


# Default metric set we always ask FW for. Maps to placement_stats columns
# through DEFAULT_COLUMN_MAP. If a publisher's report-schema labels these
# differently, the day-of-scope fix is to retune the ColumnMap rather than
# the metric names sent up.
DEFAULT_METRICS = ["impressions", "completed_views", "clicks", "ad_revenue"]


class ReportingScopeNotGranted(RuntimeError):
    """Raised when reporting sync is invoked before FW Tier 2 scope is granted.

    See ``docs/adapters/freewheel/README.md`` → "Scope grants still needed" /
    "Tier 1 — reporting".
    """

    def __init__(self) -> None:
        super().__init__(
            "FreeWheel Query Reporting API scope not granted on this account. "
            "Every /reporting/* endpoint returns AWS API Gateway IAM-deny for "
            "the current user. See docs/adapters/freewheel/README.md for the "
            "scope request. Reading paths return empty results gracefully "
            "until sync is wired."
        )


@dataclass
class ReportingSyncResult:
    """Summary of one reporting-sync run."""

    placements_updated: int
    job_id: str | None
    error: str | None = None


class FreeWheelReportingSync:
    """Drives the FreeWheel Query Reporting API → placement-stats cache flow.

    Composed by callers (admin "Sync Reporting" button or a scheduled job
    runner) with a tenant-scoped client + repository.

    Today: :meth:`run` calls through to the live API. If scope isn't
    granted the first call raises :class:`ReportingScopeNotGranted`;
    everything downstream tolerates that.
    """

    # Default lookback: report on today's pacing. Callers can widen
    # the window (e.g. weekly backfill) by passing start_date/end_date
    # explicitly to :meth:`run`.
    DEFAULT_LOOKBACK = timedelta(days=1)

    def __init__(
        self,
        client: FreeWheelClient,
        tenant_id: str,
        *,
        session: Session | None = None,
        column_map: ColumnMap = DEFAULT_COLUMN_MAP,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._client = client
        self._tenant_id = tenant_id
        self._session = session
        self._column_map = column_map
        self._poll_timeout = poll_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._reporting_client = FreeWheelReportingClient(client._transport)

    def run(
        self,
        *,
        placement_ids: list[str] | None = None,
        advertiser_ids: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ReportingSyncResult:
        """Submit, poll, fetch, upsert.

        Args:
            placement_ids: Optional narrowing — if set, the report is
                scoped to these placements. Otherwise reports against all
                placements in the tenant's network.
            advertiser_ids: Optional advertiser filter (same shape as
                JobSpec.advertiser_ids).
            start_date / end_date: Report date window. Defaults to today
                only (publishers want near-real-time pacing).

        Raises:
            ReportingScopeNotGranted: when the upstream API still IAM-denies us.
            ReportingError: for other client-level failures (job failed,
                result missing, polling timed out).
        """
        today = datetime.now(UTC).date()
        start = start_date or today
        end = end_date or today

        spec = JobSpec(
            name=f"adcp-pacing-{self._tenant_id}-{today.isoformat()}",
            dimensions=["placement_id"],
            metrics=DEFAULT_METRICS,
            start_date=start,
            end_date=end,
            placement_ids=placement_ids,
            advertiser_ids=advertiser_ids,
        )

        logger.info(
            "FreeWheel reporting sync tenant=%s placements=%s window=%s..%s",
            self._tenant_id,
            len(placement_ids) if placement_ids else "all",
            start,
            end,
        )

        try:
            submitted = self._reporting_client.submit_job(spec)
        except FreeWheelForbiddenError as exc:
            logger.info("FreeWheel reporting scope still pending for tenant=%s: %s", self._tenant_id, exc)
            raise ReportingScopeNotGranted() from exc

        logger.info("FreeWheel reporting job submitted job_id=%s status=%s", submitted.job_id, submitted.status.value)

        if not submitted.job_id:
            raise ReportingError("FreeWheel returned an empty job_id; cannot poll.")

        terminal = self._reporting_client.wait_for_completion(
            submitted.job_id,
            timeout_seconds=self._poll_timeout,
            poll_interval_seconds=self._poll_interval,
        )

        if terminal.status is JobStatus.FAILED or terminal.status is JobStatus.CANCELED:
            return ReportingSyncResult(
                placements_updated=0,
                job_id=terminal.job_id,
                error=f"Job ended in status {terminal.status.value}: {terminal.error_message or 'no detail'}",
            )

        rows = self._reporting_client.fetch_results(terminal)
        logger.info("FreeWheel reporting job %s returned %d rows", terminal.job_id, len(rows))

        updated = self._upsert_rows(rows)
        return ReportingSyncResult(placements_updated=updated, job_id=terminal.job_id)

    def _upsert_rows(self, rows: list[dict]) -> int:
        """Coerce + persist returned rows. Caller must have set ``session``."""
        if self._session is None:
            raise ReportingError(
                "FreeWheelReportingSync was constructed without a DB session; set ``session=`` to enable cache upsert."
            )

        parsed: list[dict] = []
        for row in rows:
            cleaned = parse_row(row, self._column_map)
            if not cleaned["placement_id"]:
                logger.warning("Skipping reporting row with no placement_id: %s", row)
                continue
            parsed.append(cleaned)

        repo = FreeWheelPlacementStatsRepository(self._session, self._tenant_id)
        repo.bulk_upsert(parsed)
        self._session.commit()
        return len(parsed)
