"""Sprint 1.8 §8 — cron-side cadence gating.

Verifies that ``scripts/ops/sync_all_tenants.py:should_sync_tenant``
correctly decides whether to skip a tenant on a given cron tick based
on its per-tenant ``sync_cadence_minutes`` and the most-recent
successful sync's ``completed_at``.

The decision logic is pure (no DB, no clock) so the tests construct
lightweight tenant stubs and pass an explicit ``now``. The cron loop's
SQL composition is exercised by the existing /refresh integration
tests + manual smoke runs against a Docker stack.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.ops.sync_all_tenants import (
    DEFAULT_SYNC_CADENCE_MINUTES,
    should_sync_tenant,
)

pytestmark = [pytest.mark.unit]


def _tenant(cadence: int | None) -> SimpleNamespace:
    """Build a stub matching the subset of ``Tenant`` that
    ``should_sync_tenant`` reads (``sync_cadence_minutes``)."""
    return SimpleNamespace(sync_cadence_minutes=cadence, tenant_id="tenant_test", name="Test")


class TestShouldSyncTenant:
    def test_default_cadence_is_six_hours(self):
        assert DEFAULT_SYNC_CADENCE_MINUTES == 360

    def test_null_cadence_within_default_window_skips(self):
        """sync_cadence_minutes=NULL + synced 3h ago → skip (under 360m default)."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        latest = now - timedelta(hours=3)
        should_run, cadence = should_sync_tenant(_tenant(None), latest, now)
        assert should_run is False
        assert cadence == DEFAULT_SYNC_CADENCE_MINUTES

    def test_null_cadence_past_default_window_runs(self):
        """sync_cadence_minutes=NULL + synced 7h ago → run (past 360m default)."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        latest = now - timedelta(hours=7)
        should_run, cadence = should_sync_tenant(_tenant(None), latest, now)
        assert should_run is True
        assert cadence == DEFAULT_SYNC_CADENCE_MINUTES

    def test_explicit_cadence_within_window_skips(self):
        """sync_cadence_minutes=120 + synced 90m ago → skip (within 120m window)."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        latest = now - timedelta(minutes=90)
        should_run, cadence = should_sync_tenant(_tenant(120), latest, now)
        assert should_run is False
        assert cadence == 120

    def test_explicit_cadence_past_window_runs(self):
        """sync_cadence_minutes=120 + synced 150m ago → run (past 120m window)."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        latest = now - timedelta(minutes=150)
        should_run, cadence = should_sync_tenant(_tenant(120), latest, now)
        assert should_run is True
        assert cadence == 120

    def test_never_synced_always_runs(self):
        """No prior successful sync → run regardless of cadence (initial backfill)."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        # Both default and aggressive cadences must run when latest is None.
        should_run_default, _ = should_sync_tenant(_tenant(None), None, now)
        assert should_run_default is True

        should_run_explicit, cadence = should_sync_tenant(_tenant(720), None, now)
        assert should_run_explicit is True
        assert cadence == 720

    def test_long_cadence_skips_within_12h_window(self):
        """sync_cadence_minutes=720 + synced 10h ago → skip (within 12h window)."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        latest = now - timedelta(hours=10)
        should_run, cadence = should_sync_tenant(_tenant(720), latest, now)
        assert should_run is False
        assert cadence == 720

    def test_naive_datetime_treated_as_utc(self):
        """Naive ``completed_at`` is normalized to UTC so the helper
        doesn't crash on synthesized test data."""
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        # 7h ago, naive (simulates a row whose tz info was stripped).
        latest_naive = (now - timedelta(hours=7)).replace(tzinfo=None)
        should_run, _ = should_sync_tenant(_tenant(None), latest_naive, now)
        assert should_run is True

    def test_exactly_at_window_boundary_runs(self):
        """At cadence boundary (now == latest + cadence) → run.

        Boundary semantics: ``>=`` means "the moment we hit the window
        end, the next cron tick fires." Avoids tenants with cadence
        aligned to cron interval slipping by a tick.
        """
        now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        latest = now - timedelta(minutes=120)
        should_run, _ = should_sync_tenant(_tenant(120), latest, now)
        assert should_run is True
