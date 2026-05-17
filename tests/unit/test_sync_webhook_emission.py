"""Unit tests for the sync webhook emission helpers.

Pure functions only — the SQLAlchemy listener wiring is exercised by the
integration tests at ``tests/integration/test_sync_webhook_emission.py``
where a real session commit drives the before_flush / after_commit hooks.

Issue #463.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.admin.services.sync_webhook_emission import (
    _build_payload,
    _include_traceback,
    _iso,
    _normalize_trigger,
)


class TestNormalizeTrigger:
    """The public ``trigger`` Literal is ``initial|scheduled|manual``. The
    internal ``triggered_by`` taxonomy is open-ended and grows over time,
    so the normalizer pins the surface and absorbs the churn."""

    def test_provision_id_maps_to_initial(self):
        # Provisioning sets triggered_by_id=tenant_management_api:provision
        # (see _create_and_spawn_refresh).
        assert _normalize_trigger("api", "tenant_management_api:provision") == "initial"

    def test_provision_id_wins_even_when_triggered_by_looks_manual(self):
        assert _normalize_trigger("admin_button", "tenant_management_api:provision") == "initial"

    def test_scheduler_prefix_maps_to_scheduled(self):
        assert _normalize_trigger("scheduler_reporting", None) == "scheduled"
        assert _normalize_trigger("scheduler", None) == "scheduled"

    def test_cron_maps_to_scheduled(self):
        assert _normalize_trigger("cron", None) == "scheduled"

    def test_admin_ui_maps_to_manual(self):
        assert _normalize_trigger("admin_ui", None) == "manual"

    def test_admin_button_maps_to_manual(self):
        assert _normalize_trigger("admin_button", None) == "manual"

    def test_api_refresh_maps_to_manual(self):
        # POST /tenants/{id}/refresh uses triggered_by_id=...:refresh
        assert _normalize_trigger("api", "tenant_management_api:refresh") == "manual"

    def test_worker_maps_to_manual(self):
        # gam_advertisers_sync uses triggered_by="worker"
        assert _normalize_trigger("worker", None) == "manual"

    def test_unknown_triggers_default_to_manual(self):
        assert _normalize_trigger("something_new_in_2027", None) == "manual"

    def test_none_triggers_default_to_manual(self):
        assert _normalize_trigger(None, None) == "manual"


class TestBuildPayload:
    """The data block schema is the contract agentic-api integrates against.
    A breaking change here breaks every storefront client that codegens
    from our OpenAPI."""

    def _snapshot(self, **overrides):
        base = {
            "_status": "completed",
            "tenant_id": "tnt_acme",
            "sync_run_id": "sync_001",
            "sync_type": "inventory",
            "adapter_type": "google_ad_manager",
            "started_at": datetime(2026, 5, 17, 18, 23, 11, tzinfo=UTC),
            "completed_at": datetime(2026, 5, 17, 18, 24, 33, tzinfo=UTC),
            "summary": "Synced 12345 ad units",
            "error_message": None,
            "triggered_by": "scheduler",
            "triggered_by_id": None,
            "item_count": 12345,
        }
        base.update(overrides)
        return base

    def test_completed_payload_shape(self):
        snap = self._snapshot()
        payload = _build_payload(snap, "sync.completed")
        assert payload == {
            "sync_run_id": "sync_001",
            "sync_type": "inventory",
            "adapter_type": "google_ad_manager",
            "trigger": "scheduled",
            "started_at": "2026-05-17T18:23:11+00:00",
            "completed_at": "2026-05-17T18:24:33+00:00",
            "item_count": 12345,
            "summary": "Synced 12345 ad units",
        }

    def test_completed_payload_omits_error_block(self):
        snap = self._snapshot()
        payload = _build_payload(snap, "sync.completed")
        assert "error" not in payload

    def test_failed_payload_shape(self):
        snap = self._snapshot(
            _status="failed",
            error_message="Refresh token revoked",
            item_count=None,
            summary=None,
            completed_at=datetime(2026, 5, 17, 18, 24, 0, tzinfo=UTC),
        )
        payload = _build_payload(snap, "sync.failed")
        assert payload["error"] == {"message": "Refresh token revoked"}
        # item_count and summary are completed-only fields
        assert "item_count" not in payload
        assert "summary" not in payload

    def test_failed_payload_carries_required_envelope_fields(self):
        snap = self._snapshot(_status="failed", error_message="boom")
        payload = _build_payload(snap, "sync.failed")
        # The data block must always carry the run identity + timing so the
        # receiver can correlate to its own UI state without an extra read.
        for key in ("sync_run_id", "sync_type", "adapter_type", "trigger", "started_at", "completed_at"):
            assert key in payload, f"missing required key {key} in failure payload"

    def test_completed_with_no_item_count_emits_none(self):
        snap = self._snapshot(item_count=None)
        payload = _build_payload(snap, "sync.completed")
        # Receivers should expect the key present with a null value,
        # not omitted — keeps generated TS/Python types happy.
        assert payload["item_count"] is None

    def test_failed_without_error_message_still_emits_error_block(self):
        # If error_message is None (rare but possible — e.g. a stale-row
        # cleanup that didn't capture an exception), we still emit the
        # error block with a null message rather than dropping it.
        # Receivers should never have to special-case missing 'error'.
        snap = self._snapshot(_status="failed", error_message=None, item_count=None, summary=None)
        payload = _build_payload(snap, "sync.failed")
        assert payload["error"] == {"message": None}

    def test_traceback_omitted_by_default(self, monkeypatch):
        # Production publishers don't want stack frames leaking through
        # the webhook surface.
        monkeypatch.delenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        snap = self._snapshot(_status="failed", error_message="boom\n  at frame")
        payload = _build_payload(snap, "sync.failed")
        assert "traceback" not in payload["error"]

    def test_traceback_included_when_dev_flag_set(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", "true")
        snap = self._snapshot(_status="failed", error_message="boom\n  at frame")
        payload = _build_payload(snap, "sync.failed")
        assert payload["error"]["traceback"] == "boom\n  at frame"

    def test_traceback_included_in_development_environment(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        snap = self._snapshot(_status="failed", error_message="boom")
        payload = _build_payload(snap, "sync.failed")
        assert "traceback" in payload["error"]


class TestIsoRendering:
    """Datetimes ride through JSON as ISO-8601 strings. Receivers parse
    these into typed datetime — a leaking ``None``-vs-empty-string or a
    naive timestamp would shift them by their local offset on parse."""

    def test_aware_datetime_includes_offset(self):
        dt = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
        assert _iso(dt) == "2026-05-17T12:00:00+00:00"

    def test_none_passes_through(self):
        assert _iso(None) is None


class TestIncludeTraceback:
    """The flag governs whether failure tracebacks travel on the webhook
    wire — off by default in production."""

    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        assert _include_traceback() is False

    def test_explicit_flag_on(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", "true")
        assert _include_traceback() is True

    def test_development_environment_on(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert _include_traceback() is True

    def test_production_environment_off(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert _include_traceback() is False
