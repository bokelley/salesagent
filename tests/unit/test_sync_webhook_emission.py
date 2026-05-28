"""Unit tests for the sync webhook emission helpers.

Pure functions only — the SQLAlchemy listener wiring is exercised by the
integration tests at ``tests/integration/test_managed_tenant_api_sprint6.py``
where a real session commit drives the before_flush / after_commit hooks.

Issue #463.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.admin.services.sync_webhook_emission import (
    _build_payload,
    _classify_error,
    _dedup_snapshots,
    _dispatch_one,
    _iso,
    _normalize_trigger,
    _public_error_message,
)


class TestNormalizeTrigger:
    """The public ``trigger`` Literal is
    ``provisioning|scheduled|manual|unknown``. The internal
    ``triggered_by`` taxonomy is open-ended and grows over time, so the
    normalizer pins the surface and absorbs the churn. ``unknown`` is
    the default for unmatched labels so receivers can detect drift
    instead of silently misattributing to a user action."""

    def test_provision_id_maps_to_provisioning(self):
        # Provisioning sets triggered_by_id=tenant_management_api:provision
        # (see _create_and_spawn_refresh).
        assert _normalize_trigger("api", "tenant_management_api:provision") == "provisioning"

    def test_provision_id_wins_even_when_triggered_by_looks_manual(self):
        assert _normalize_trigger("admin_button", "tenant_management_api:provision") == "provisioning"

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

    def test_unknown_triggers_default_to_unknown(self):
        # Drift signal — receivers see ``unknown`` instead of silently
        # absorbing a new label into ``manual``.
        assert _normalize_trigger("something_new_in_2027", None) == "unknown"

    def test_none_triggers_default_to_unknown(self):
        assert _normalize_trigger(None, None) == "unknown"


class TestClassifyError:
    """Coarse 3-bucket classification of ``error_message`` so storefront
    UIs can pick a CTA (Retry / Reconnect / Contact admin) without
    substring-matching our exception strings."""

    def test_none_message_is_permanent(self):
        # No error context = no claim of recoverability; surface as
        # ``permanent`` so the UI defaults to "needs attention".
        assert _classify_error(None) == "permanent"

    def test_oauth_revoked_is_auth(self):
        assert _classify_error("Refresh token revoked") == "auth"

    def test_invalid_grant_is_auth(self):
        assert _classify_error("OAuth error: invalid_grant") == "auth"

    def test_gam_403_is_auth(self):
        assert _classify_error("GAM 403: insufficient permissions to read") == "auth"

    def test_timeout_is_transient(self):
        assert _classify_error("Worker spawn failed: TimeoutError: hung") == "transient"

    def test_rate_limit_is_transient(self):
        assert _classify_error("Rate limit exceeded for advertiser sync") == "transient"

    def test_503_is_transient(self):
        assert _classify_error("HTTP 503 Service Unavailable") == "transient"

    def test_unknown_string_defaults_permanent(self):
        # A novel error shape doesn't claim recoverability — receivers
        # should treat it as needing operator attention until classified.
        assert _classify_error("AdServerInvariantViolated: bid floor mismatch") == "permanent"

    def test_classifier_is_case_insensitive(self):
        assert _classify_error("REFRESH TOKEN REVOKED") == "auth"
        assert _classify_error("TIMEOUT") == "transient"


class TestPublicErrorMessage:
    """The webhook subscriber may be a third-party endpoint (Slack
    channel, generic ingestion, etc.) that shouldn't see internal stack
    frames or adapter response details. The scrubber turns operator-
    facing strings into a single short line."""

    def test_none_passes_through(self):
        assert _public_error_message(None) is None

    def test_empty_string_returns_none(self):
        assert _public_error_message("") is None

    def test_keeps_short_single_line_intact(self):
        assert _public_error_message("Refresh token revoked") == "Refresh token revoked"

    def test_strips_traceback_to_first_line(self):
        raw = (
            "Worker spawn failed (inventory): TimeoutError: GAM request hung\n\n"
            "Traceback (most recent calls):\n"
            '  File "src/services/background_sync_service.py", line 50, in start\n'
            "    self._run()\n"
        )
        scrubbed = _public_error_message(raw)
        assert scrubbed == "Worker spawn failed (inventory): TimeoutError: GAM request hung"
        assert "Traceback" not in scrubbed
        assert "File " not in scrubbed

    def test_caps_long_single_line(self):
        raw = "x" * 1000
        scrubbed = _public_error_message(raw)
        # Public cap is 200 chars per _MAX_PUBLIC_ERROR_LEN.
        assert scrubbed is not None
        assert len(scrubbed) == 200

    def test_strips_surrounding_whitespace(self):
        assert _public_error_message("   boom   \n   next   ") == "boom"


class TestDedupSnapshots:
    """Multiple ``before_flush`` invocations on one transaction can
    capture the same terminal transition twice. The drain step
    collapses by (tenant_id, sync_run_id, _status)."""

    def _snap(self, **overrides):
        from tests.helpers.sync_webhook_emission import make_snapshot

        defaults = {"tenant_id": "tnt_a", "sync_run_id": "sync_1"}
        defaults.update(overrides)
        return make_snapshot(**defaults)

    def test_empty_list_returns_empty(self):
        assert _dedup_snapshots([]) == []

    def test_distinct_keys_pass_through(self):
        snaps = [
            self._snap(sync_run_id="sync_1"),
            self._snap(sync_run_id="sync_2"),
        ]
        assert _dedup_snapshots(snaps) == snaps

    def test_identical_keys_collapse_to_first(self):
        first = self._snap(sync_run_id="sync_1", summary="first capture")
        second = self._snap(sync_run_id="sync_1", summary="second capture")
        out = _dedup_snapshots([first, second])
        # First occurrence wins so later flushes of the same row don't
        # multiply emissions.
        assert len(out) == 1
        assert out[0]["summary"] == "first capture"

    def test_same_run_different_status_does_not_collapse(self):
        # Edge case — a single txn that pushes a row through more than
        # one terminal state would emit each. Not a real flow today
        # but the dedup key correctly preserves it.
        snaps = [
            self._snap(_status="completed"),
            self._snap(_status="failed"),
        ]
        out = _dedup_snapshots(snaps)
        assert len(out) == 2

    def test_cross_tenant_does_not_collapse(self):
        snaps = [
            self._snap(tenant_id="tnt_a", sync_run_id="sync_1"),
            self._snap(tenant_id="tnt_b", sync_run_id="sync_1"),
        ]
        out = _dedup_snapshots(snaps)
        assert len(out) == 2


class TestBuildPayload:
    """The data block schema is the contract agentic-api integrates against.
    A breaking change here breaks every storefront client that codegens
    from our OpenAPI."""

    def _snapshot(self, **overrides):
        from tests.helpers.sync_webhook_emission import make_snapshot

        defaults = {
            "tenant_id": "tnt_acme",
            "sync_run_id": "sync_001",
            "started_at": datetime(2026, 5, 17, 18, 23, 11, tzinfo=UTC),
            "completed_at": datetime(2026, 5, 17, 18, 24, 33, tzinfo=UTC),
            "summary": "Synced 12345 ad units",
            "triggered_by": "scheduler",
            "item_count": 12345,
        }
        defaults.update(overrides)
        return make_snapshot(**defaults)

    def test_completed_payload_shape(self):
        snap = self._snapshot()
        payload = _build_payload(snap, "sync_run.completed")
        assert payload == {
            "sync_run_id": "sync_001",
            "sync_type": "inventory",
            "adapter_type": "google_ad_manager",
            "status": "completed",
            "trigger": "scheduled",
            "started_at": "2026-05-17T18:23:11+00:00",
            "completed_at": "2026-05-17T18:24:33+00:00",
            "item_count": 12345,
            "summary": "Synced 12345 ad units",
        }

    def test_completed_payload_omits_error_block(self):
        snap = self._snapshot()
        payload = _build_payload(snap, "sync_run.completed")
        assert "error" not in payload

    def test_failed_payload_shape(self):
        snap = self._snapshot(
            _status="failed",
            error_message="Refresh token revoked",
            item_count=None,
            summary=None,
            completed_at=datetime(2026, 5, 17, 18, 24, 0, tzinfo=UTC),
        )
        payload = _build_payload(snap, "sync_run.failed")
        # error.class is always-null today (reserved for structured
        # exception capture); error.category classifies the message so
        # storefront UIs can pick a CTA without substring matching.
        assert payload["error"] == {
            "message": "Refresh token revoked",
            "class": None,
            "category": "auth",
        }
        # item_count and summary are completed-only fields
        assert "item_count" not in payload
        assert "summary" not in payload

    def test_failed_payload_carries_required_envelope_fields(self):
        snap = self._snapshot(_status="failed", error_message="boom")
        payload = _build_payload(snap, "sync_run.failed")
        # The data block must always carry the run identity + timing so the
        # receiver can correlate to its own UI state without an extra read.
        for key in ("sync_run_id", "sync_type", "adapter_type", "status", "trigger", "started_at", "completed_at"):
            assert key in payload, f"missing required key {key} in failure payload"

    def test_completed_with_no_item_count_emits_none(self):
        snap = self._snapshot(item_count=None)
        payload = _build_payload(snap, "sync_run.completed")
        # Receivers should expect the key present with a null value,
        # not omitted — keeps generated TS/Python types happy.
        assert payload["item_count"] is None

    def test_failed_without_error_message_still_emits_full_error_block(self):
        # If error_message is None (rare but possible — e.g. a stale-row
        # cleanup that didn't capture an exception), we still emit the
        # error block with all known keys present so receivers don't
        # have to special-case missing 'error' or its sub-fields.
        snap = self._snapshot(_status="failed", error_message=None, item_count=None, summary=None)
        payload = _build_payload(snap, "sync_run.failed")
        assert payload["error"] == {
            "message": None,
            "class": None,
            "category": "permanent",
        }

    def test_failed_payload_scrubs_traceback_from_error_message(self):
        """Stack frames stored in ``SyncJob.error_message`` (e.g. from the
        spawn-failure path that packs a traceback into the field) MUST
        NOT cross the webhook boundary — internal frames could carry
        file paths, advertiser IDs, or adapter response detail that the
        subscriber's endpoint shouldn't receive. The scrubber keeps the
        first line only."""
        raw = (
            "Worker spawn failed (inventory): TimeoutError\n\n"
            "Traceback (most recent calls):\n"
            '  File "/app/src/services/background_sync_service.py", line 50\n'
        )
        snap = self._snapshot(_status="failed", error_message=raw, item_count=None, summary=None)
        payload = _build_payload(snap, "sync_run.failed")
        assert payload["error"]["message"] == "Worker spawn failed (inventory): TimeoutError"
        assert "Traceback" not in payload["error"]["message"]
        # The traceback field was removed from the contract — gating by
        # env var was leaky (global flag forwarded all tenants' frames).
        assert "traceback" not in payload["error"]
        # The traceback-classified TimeoutError surfaces as transient.
        assert payload["error"]["category"] == "transient"


class TestDispatchOne:
    def test_emits_captured_health_payload_without_recomputing(self, monkeypatch):
        from tests.helpers.sync_webhook_emission import make_snapshot

        calls = []

        def fake_emit_event(tenant_id, event_type, payload):
            calls.append((tenant_id, event_type, payload))

        monkeypatch.setattr("src.admin.services.webhook_publisher.emit_event", fake_emit_event)
        health_payload = {
            "sync_type": "inventory",
            "adapter_type": "google_ad_manager",
            "health": "critical",
            "previous_health": "warning",
        }
        snap = make_snapshot(
            _status="failed",
            tenant_id="tnt_health",
            sync_run_id="sync_health_failed",
            _health_change_payload=health_payload,
        )

        _dispatch_one(snap)

        assert calls[0][0] == "tnt_health"
        assert calls[0][1] == "sync_run.failed"
        assert calls[0][2]["sync_run_id"] == "sync_health_failed"
        assert calls[1] == ("tnt_health", "sync_health.changed", health_payload)


class TestIsoRendering:
    """Datetimes ride through JSON as ISO-8601 strings. Receivers parse
    these into typed datetime — a leaking ``None``-vs-empty-string or a
    naive timestamp would shift them by their local offset on parse."""

    def test_aware_datetime_includes_offset(self):
        dt = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
        assert _iso(dt) == "2026-05-17T12:00:00+00:00"

    def test_none_passes_through(self):
        assert _iso(None) is None
