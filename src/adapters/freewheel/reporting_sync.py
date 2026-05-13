"""FreeWheel Query Reporting API sync — skeleton.

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

When the FW Reporting API scope is granted, this module will:

1. Submit a job covering all placements for the tenant (impressions,
   completed_views, spend, by placement, today or today-minus-N hours).
2. Poll the job until COMPLETE.
3. Fetch the result.
4. Bulk-upsert into ``freewheel_placement_stats`` via
   :class:`FreeWheelPlacementStatsRepository`.

Today this module is a stub. Calling :meth:`run` raises
``ReportingScopeNotGranted`` with a pointer to the README. The downstream
reader paths (``get_packages_snapshot``, ``get_media_buy_delivery``)
already tolerate an empty cache, so nothing breaks if the sync isn't
running.

When scope arrives, the only new work is filling in
``_submit_job`` / ``_poll_job`` / ``_fetch_results`` / ``_parse_rows``
against the real FW Query Reporting endpoint. Everything around that —
the read paths, the repository, the cache schema, the AdCP-spec
output shapes — is already in place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.adapters.freewheel.client import FreeWheelClient

logger = logging.getLogger(__name__)


class ReportingScopeNotGranted(RuntimeError):
    """Raised when reporting sync is invoked before FW Tier 2 scope is granted.

    See ``docs/adapters/freewheel/README.md`` → "Scope grants still needed" /
    "Tier 2 — unblocks reporting".
    """

    def __init__(self) -> None:
        super().__init__(
            "FreeWheel Query Reporting API scope not granted on this account. "
            "See docs/adapters/freewheel/README.md for the scope request. "
            "Reading paths return empty results gracefully until sync is wired."
        )


@dataclass
class ReportingSyncResult:
    """Summary of one reporting-sync run."""

    placements_updated: int
    job_id: str | None
    error: str | None = None


class FreeWheelReportingSync:
    """Drives the FreeWheel Query Reporting API → placement-stats cache flow.

    Composed by callers (the scheduled job runner) with a tenant-scoped
    transport client and repository. Today the ``run`` method short-circuits
    with :class:`ReportingScopeNotGranted` — the structural wiring is here
    so day-of-scope is a single class to implement, not a full subsystem.
    """

    def __init__(self, client: FreeWheelClient, tenant_id: str) -> None:
        self._client = client
        self._tenant_id = tenant_id

    def run(self, placement_ids: list[str] | None = None) -> ReportingSyncResult:
        """Submit, poll, fetch, upsert. Currently scope-blocked.

        Args:
            placement_ids: Optional narrowing — if set, the report job is
                scoped to these placements. Otherwise, all placements in
                the tenant's network are reported. The full-scope path
                is the common one (publishers want pacing on everything).

        Raises:
            ReportingScopeNotGranted: until Tier 2 FW scope is granted.
        """
        logger.info(
            "FreeWheel reporting sync invoked for tenant=%s placements=%s — scope grant pending, see README",
            self._tenant_id,
            len(placement_ids) if placement_ids else "all",
        )
        raise ReportingScopeNotGranted()
