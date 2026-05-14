"""Unit tests for the shared adapter sync orchestration (#382 Stage 3).

DB-touching tests (SyncJob persistence) live in
``tests/integration/test_adapter_sync_orchestration.py``. This file
covers the contract corners that don't require the database.
"""

from __future__ import annotations

import pytest

from src.services.adapter_sync_orchestration import (
    KIND_INVENTORY,
    KIND_REPORTING,
    AdapterDoesNotSupportSyncKind,
    SyncExecutionResult,
    _sanitize_error_message,
    execute_sync,
)
from tests.helpers.sync_orchestration import make_mock_adapter as _mock_adapter


class TestCapabilityGating:
    """execute_sync rejects sync_kinds the adapter hasn't declared
    support for — fail fast at the orchestration boundary rather than
    inside the adapter."""

    def test_inventory_sync_rejected_when_capability_off(self):
        adapter = _mock_adapter(supports_inventory=False)
        with pytest.raises(AdapterDoesNotSupportSyncKind) as exc:
            execute_sync(adapter=adapter, tenant_id="t1", sync_kind=KIND_INVENTORY, triggered_by="test")
        assert "supports_inventory_sync" in str(exc.value)
        adapter.run_inventory_sync.assert_not_called()

    def test_reporting_sync_rejected_when_capability_off(self):
        adapter = _mock_adapter(supports_reporting=False)
        with pytest.raises(AdapterDoesNotSupportSyncKind):
            execute_sync(adapter=adapter, tenant_id="t1", sync_kind=KIND_REPORTING, triggered_by="test")

    def test_unknown_sync_kind_rejected_with_valueerror(self):
        adapter = _mock_adapter(supports_inventory=True)
        with pytest.raises(ValueError, match="sync_kind"):
            execute_sync(adapter=adapter, tenant_id="t1", sync_kind="foobar", triggered_by="test")


class TestSanitizeErrorMessage:
    """Cross-tenant scheduling view renders ``error_message`` in plain
    text, so adapter exceptions must not leak secrets or pathological
    traceback strings into other tenants' admin views."""

    def test_pem_block_redacted(self):
        msg = "Failed: -----BEGIN PRIVATE KEY-----\nABCDEFG\n-----END PRIVATE KEY-----"
        out = _sanitize_error_message(msg)
        assert "[redacted]" in out
        assert "ABCDEFG" not in out

    def test_jwt_redacted(self):
        # Realistic JWT shape — 3 base64url segments separated by dots,
        # header segment alone >20 chars, payload and signature each >10.
        msg = (
            "Bad token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c rejected"
        )
        out = _sanitize_error_message(msg)
        assert "[redacted]" in out
        assert "eyJhbGciOi" not in out

    def test_refresh_token_kv_redacted(self):
        msg = 'ConnectionError: refresh_token="1//0abcdef1234567890xyz" rejected'
        out = _sanitize_error_message(msg)
        assert "[redacted]" in out
        assert "0abcdef1234567890xyz" not in out

    def test_long_message_truncated(self):
        long_msg = "X" * 2000
        out = _sanitize_error_message(long_msg)
        assert len(out) <= 500
        assert out.endswith("…")

    def test_short_clean_message_passes_through(self):
        msg = "scope: Tier 1 reporting scope still pending"
        assert _sanitize_error_message(msg) == msg

    def test_empty_message_passes_through(self):
        assert _sanitize_error_message("") == ""


class TestSyncExecutionResultJsonPayload:
    """Canonical JSON shape shared between FW endpoints + scheduling
    Run Now so the next adapter button doesn't reinvent a third shape."""

    def test_payload_includes_canonical_fields(self):
        result = SyncExecutionResult(
            sync_id="sync_x",
            sync_kind="reporting",
            succeeded=False,
            counts={"placements": 0},
            errors={"scope": "denied"},
            metadata={"scope_pending": True},
        )
        payload = result.to_json_payload()
        assert payload["sync_id"] == "sync_x"
        assert payload["sync_kind"] == "reporting"
        assert payload["succeeded"] is False
        assert payload["counts"] == {"placements": 0}
        assert payload["errors"] == {"scope": "denied"}
        assert payload["scope_pending"] is True
        # Dict-copy semantics: mutating payload doesn't mutate the result.
        payload["counts"]["placements"] = 999
        assert result.counts == {"placements": 0}
