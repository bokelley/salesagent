"""Tests for FreeWheelAdapter.check_permissions() — operator-facing probe.

Covers:
- Dry-run mode short-circuits (no live API calls)
- Granted/denied mapping uses HTTP status, not response body
- 4xx validation errors (400/404/422) count as granted — they prove the
  endpoint accepts the call, just missing a query param
- Auth failures bail the whole pass with ``error`` set
- ``fully_operational`` rolls up only over ``required=True`` probes
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.base import PermissionsReport
from src.adapters.freewheel import FreeWheelAdapter
from src.adapters.freewheel.client import FreeWheelAuthError


@pytest.fixture
def mock_principal():
    p = MagicMock()
    p.principal_id = "p1"
    p.get_adapter_id.return_value = "1356511"
    p.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}
    return p


class TestCheckPermissionsDryRun:
    """In dry-run mode there's no live client; the probe short-circuits and
    surfaces the situation as ``error``, not as a wall of fake passes."""

    def test_dry_run_returns_error_report(self, mock_principal):
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=True, tenant_id="t1")
        report = adapter.check_permissions()
        assert isinstance(report, PermissionsReport)
        assert report.adapter == "freewheel"
        assert report.tenant_id == "t1"
        assert report.fully_operational is False
        assert report.error is not None
        assert "Dry-run" in report.error
        assert report.checks == []


class TestCheckPermissionsLive:
    """Permission probe semantics with a mocked transport."""

    def _adapter_with_probe_responses(self, mock_principal, responses_by_path):
        """Build a live-mode adapter whose transport.probe returns the
        per-endpoint statuses ``responses_by_path`` (a dict path-prefix → status)."""
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=False, tenant_id="t1")

        def fake_probe(method, path, accept="application/json"):
            # Match the longest prefix so we can override specific probes
            for prefix in sorted(responses_by_path.keys(), key=len, reverse=True):
                if path.startswith(prefix):
                    status = responses_by_path[prefix]
                    body = '{"Message":"User is not authorized..."}' if status == 403 else ""
                    return status, body
            # Default: granted
            return 200, ""

        adapter._client._transport.probe = fake_probe
        return adapter

    def test_all_granted_marks_fully_operational(self, mock_principal):
        adapter = self._adapter_with_probe_responses(mock_principal, {"/": 200})
        report = adapter.check_permissions()
        assert report.fully_operational is True
        assert report.error is None
        assert all(c.granted for c in report.checks)

    def test_required_denial_blocks_fully_operational(self, mock_principal):
        """A 403 on /services/v4/creative_instances (required) flips
        fully_operational to False even when every other probe passes."""
        adapter = self._adapter_with_probe_responses(
            mock_principal,
            {"/services/v4/creative_instances": 403, "/": 200},
        )
        report = adapter.check_permissions()
        assert report.fully_operational is False
        ci = next(c for c in report.checks if c.name == "v4_creative_instances")
        assert ci.granted is False
        assert ci.required is True
        assert ci.detail is not None and "403" in ci.detail

    def test_optional_denial_does_not_block_fully_operational(self, mock_principal):
        """Nice-to-haves like reporting deny without breaking ``fully_operational``."""
        adapter = self._adapter_with_probe_responses(
            mock_principal,
            {"/reporting/": 403, "/": 200},
        )
        report = adapter.check_permissions()
        assert report.fully_operational is True
        reporting = next(c for c in report.checks if c.name == "reporting_jobs")
        assert reporting.granted is False
        assert reporting.required is False

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_validation_errors_count_as_granted(self, mock_principal, status):
        """The endpoint accepts the call — just needs a different param. That's
        a permission grant, not a denial. Discovery probes use minimal payloads
        so 4xx validation responses are expected on some probes."""
        adapter = self._adapter_with_probe_responses(
            mock_principal,
            {"/services/v4/creative_instances": status, "/": 200},
        )
        report = adapter.check_permissions()
        ci = next(c for c in report.checks if c.name == "v4_creative_instances")
        assert ci.granted is True
        assert report.fully_operational is True

    def test_401_counts_as_denial(self, mock_principal):
        adapter = self._adapter_with_probe_responses(
            mock_principal,
            {"/services/v4/creative_instances": 401, "/": 200},
        )
        report = adapter.check_permissions()
        ci = next(c for c in report.checks if c.name == "v4_creative_instances")
        assert ci.granted is False

    def test_auth_failure_bails_with_error_set(self, mock_principal):
        """If the bearer is invalid the whole pass aborts; we don't pretend
        every endpoint is denied, that'd mislead operators."""
        adapter = FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=False, tenant_id="t1")

        def raise_auth(*args, **kwargs):
            raise FreeWheelAuthError("token expired")

        adapter._client._transport.probe = raise_auth
        report = adapter.check_permissions()
        assert report.fully_operational is False
        assert report.error is not None and "Authentication failed" in report.error

    def test_probe_target_strips_query_params(self, mock_principal):
        """``probe_target`` is for human eyes — clean it up so the operator
        sees the endpoint family, not our internal one-row pagination params."""
        adapter = self._adapter_with_probe_responses(mock_principal, {"/": 200})
        report = adapter.check_permissions()
        sites = next(c for c in report.checks if c.name == "v4_inventory_sites")
        assert sites.probe_target == "GET /services/v4/sites"
        assert "?" not in sites.probe_target

    def test_every_check_carries_a_feature_label(self, mock_principal):
        """The feature label is what the AdCP-aware operator UI groups by —
        every probe must declare which AdCP capability it unlocks."""
        adapter = self._adapter_with_probe_responses(mock_principal, {"/": 200})
        report = adapter.check_permissions()
        assert report.checks  # non-empty
        for c in report.checks:
            assert c.feature is not None, f"check {c.name} has no feature"
