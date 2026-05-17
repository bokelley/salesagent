"""Tests for the synchronous credential probes used at provision time.

``probe_adapter_connection()`` is the gate that turns "bad credentials" into
a 400 at provision rather than an eternally-pending inventory sync. These
tests pin the contract for each adapter type:

- Auth rejection → ``ProbeResult(success=False, error_code="invalid_credentials")``
- Permission denied (valid creds, wrong account) → ``error_code="permission_denied"``
- Wrong network identifier (e.g. typo) → ``error_code="network_not_found"``
- Transport failure / fallback → ``error_code="connection_failed"``
- Success → ``ProbeResult(success=True)``
- Missing required config → fail with ``invalid_config`` and no HTTP call

The probes themselves call into live adapter clients; tests mock those at
the call boundary so the behavior under each HTTP outcome is exercised.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.admin.services.adapter_connection_tester import (
    CONNECTION_FAILED,
    INVALID_CONFIG,
    INVALID_CREDENTIALS,
    NETWORK_NOT_FOUND,
    PERMISSION_DENIED,
    _classify_gam_message,
    preview_adapter,
    probe_adapter_connection,
)


class TestGAMFaultClassification:
    """``_classify_gam_message`` is the heart of #467: it turns a GAM SOAP
    fault string into a typed sub-code + structured fault block so callers
    don't have to grep English error text."""

    def test_network_not_found_classified_as_typo(self):
        msg = "Authentication failed: [AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'3312659540']"
        code, fault = _classify_gam_message(msg)
        assert code == NETWORK_NOT_FOUND
        assert fault["service"] == "AuthenticationError"
        assert fault["reason"] == "NETWORK_NOT_FOUND"
        assert fault["trigger"] == "3312659540"

    def test_not_allowed_classified_as_permission_denied(self):
        msg = "Authentication failed: [AuthenticationError.NOT_ALLOWED @ network]"
        code, fault = _classify_gam_message(msg)
        assert code == PERMISSION_DENIED
        assert fault["reason"] == "NOT_ALLOWED"

    def test_no_networks_to_access_classified_as_permission_denied(self):
        msg = "[AuthenticationError.NO_NETWORKS_TO_ACCESS @ ]"
        code, fault = _classify_gam_message(msg)
        assert code == PERMISSION_DENIED
        assert fault["reason"] == "NO_NETWORKS_TO_ACCESS"

    def test_authentication_failed_classified_as_invalid_credentials(self):
        msg = "[AuthenticationError.AUTHENTICATION_FAILED @ ]"
        code, fault = _classify_gam_message(msg)
        assert code == INVALID_CREDENTIALS
        assert fault["reason"] == "AUTHENTICATION_FAILED"

    def test_unparseable_message_falls_back_to_connection_failed(self):
        code, fault = _classify_gam_message("DNS lookup failed for adsapi.google.com")
        assert code == CONNECTION_FAILED
        assert fault == {}

    def test_unknown_reason_falls_back_to_connection_failed(self):
        msg = "[ServerError.SOAP_FAULT @ ; trigger:'something']"
        code, fault = _classify_gam_message(msg)
        assert code == CONNECTION_FAILED
        # Fault block is still populated for diagnostics.
        assert fault["service"] == "ServerError"
        assert fault["reason"] == "SOAP_FAULT"

    def test_multi_fault_prefers_classifiable_reason(self):
        """Real GAM responses often prepend a generic ``ServerError`` wrapper
        in front of the diagnostic ``AuthenticationError`` entry. The
        classifier must scan past the wrapper to find the typed reason —
        otherwise the whole point of #467 is silently defeated."""
        msg = "Server fault: [ServerError.SOAP_FAULT @ ] [AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'12345']"
        code, fault = _classify_gam_message(msg)
        assert code == NETWORK_NOT_FOUND
        # Picks the classifiable entry, not the first one.
        assert fault["reason"] == "NETWORK_NOT_FOUND"
        assert fault["trigger"] == "12345"


class TestFreeWheelProbe:
    """FreeWheel probe is two-call: token_info (auth) + list_sites (binding)."""

    def _config(self, **overrides):
        base = {"api_token": "tok", "environment": "production"}
        base.update(overrides)
        return base

    def test_missing_credentials_fails_with_invalid_config(self):
        result = probe_adapter_connection("freewheel", {"environment": "production"})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "username + password" in result.error_message or "api_token" in result.error_message

    def test_auth_rejection_classified_as_invalid_credentials(self):
        from src.adapters.freewheel._transport import FreeWheelAuthError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelAuthError("bad token", status_code=401)
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in result.error_message

    def test_inventory_403_classified_as_permission_denied(self):
        from src.adapters.freewheel._transport import FreeWheelForbiddenError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.side_effect = FreeWheelForbiddenError("no inventory scope", status_code=403)
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert "cannot read inventory" in result.error_message
        assert "publisher" in result.error_message

    def test_transport_failure_classified_as_connection_failed(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = ConnectionError("DNS")
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == CONNECTION_FAILED
        assert "transport failure" in result.error_message

    def test_happy_path_returns_success(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.return_value = MagicMock()
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is True
        assert result.error_code is None
        assert result.error_message is None


class TestBroadstreetProbe:
    """Broadstreet probe is one call: get_network() validates auth + binding."""

    def test_missing_network_id_fails_with_invalid_config(self):
        result = probe_adapter_connection("broadstreet", {"api_key": "k"})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "network_id" in result.error_message

    def test_missing_api_key_fails_with_invalid_config(self):
        result = probe_adapter_connection("broadstreet", {"network_id": "123"})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "api_key" in result.error_message

    def test_401_classified_as_invalid_credentials(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("unauthorized", status_code=401)
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "wrong"})
        assert result.success is False
        assert result.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in result.error_message

    def test_403_classified_as_permission_denied(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("forbidden", status_code=403)
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "wrong"})
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert "network access denied" in result.error_message

    def test_wrong_network_id_classified_as_network_not_found(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("not found", status_code=404)
            result = probe_adapter_connection("broadstreet", {"network_id": "999999", "api_key": "k"})
        assert result.success is False
        assert result.error_code == NETWORK_NOT_FOUND
        assert "not found" in result.error_message
        assert "999999" in result.error_message

    def test_happy_path_returns_success(self):
        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.return_value = {"id": 123, "name": "Net"}
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "k"})
        assert result.success is True
        assert result.error_code is None


class TestSpringServeProbe:
    """SpringServe probe is one transport.probe() call — status code drives
    the outcome. Auth-mint failures from the password grant raise rather
    than returning a status code."""

    def _config(self, **overrides):
        base = {"api_token": "tok"}
        base.update(overrides)
        return base

    def test_missing_credentials_fails_with_invalid_config(self):
        result = probe_adapter_connection("springserve", {})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "email + password" in result.error_message or "api_token" in result.error_message

    def test_auth_mint_failure_classified_as_invalid_credentials(self):
        from src.adapters.springserve._transport import SpringServeAuthError

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.side_effect = SpringServeAuthError("bad creds", status_code=401)
            result = probe_adapter_connection("springserve", {"email": "a@b.com", "password": "x"})
        assert result.success is False
        assert result.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in result.error_message

    def test_403_classified_as_permission_denied(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (403, "Forbidden")
            result = probe_adapter_connection("springserve", self._config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert "cannot read supply inventory" in result.error_message
        assert "publisher" in result.error_message

    def test_happy_path_returns_success(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (200, "[]")
            result = probe_adapter_connection("springserve", self._config())
        assert result.success is True
        assert result.error_code is None


class TestRoutingTable:
    """The dispatch in probe_adapter_connection covers every adapter the
    discriminated AdapterConfig union accepts. Adding a new adapter to the
    schema without updating this dispatch is a real (and previously latent)
    bug — this guard catches it by deriving the adapter list directly from
    the schema's discriminated union, so a hardcoded list can't fall out
    of sync."""

    def test_all_adapter_types_are_routed(self):
        from typing import get_args

        from src.admin.api_schemas.tenant_management import AdapterConfig

        # AdapterConfig is Annotated[Union[...], Field(discriminator="type")].
        # Unwrap to get the union, then pull each member's "type" Literal.
        union = get_args(AdapterConfig)[0]
        adapter_types = [get_args(m.model_fields["type"].annotation)[0] for m in get_args(union)]
        assert adapter_types, "Schema introspection returned no adapter types — check AdapterConfig union shape"

        for adapter_type in adapter_types:
            result = probe_adapter_connection(adapter_type, {})
            err = result.error_message or ""
            assert "Unsupported adapter_type" not in err, (
                f"{adapter_type!r} (declared in AdapterConfig union) fell through to the "
                f"unsupported-type branch in probe_adapter_connection — add a probe for it."
            )

    def test_all_adapter_types_have_preview(self):
        """Same guard for ``preview_adapter`` — the Storefront's inline
        preview UI must work for every adapter the schema accepts. A new
        adapter without a preview path returns ``ok=False`` with an
        Unsupported error, breaking the pre-commit UX."""
        from typing import get_args

        from src.admin.api_schemas.tenant_management import AdapterConfig

        union = get_args(AdapterConfig)[0]
        adapter_types = [get_args(m.model_fields["type"].annotation)[0] for m in get_args(union)]

        for adapter_type in adapter_types:
            preview = preview_adapter(adapter_type, {})
            err = preview.error or ""
            assert "Unsupported adapter_type" not in err, (
                f"{adapter_type!r} (declared in AdapterConfig union) fell through to the "
                f"unsupported-type branch in preview_adapter — add a _preview_{adapter_type}()."
            )


class TestGAMProbeClassification:
    """End-to-end: a GAM-flavored exception bubbles up through ``_test_gam``
    and gets classified into the right ``error_code``. This is the
    user-visible contract from #467."""

    def _gam_config(self):
        return {"network_code": "12345", "service_account_json": "{}"}

    def test_network_not_found_surface_through_probe(self):
        with patch("src.adapters.gam.client.GAMClientManager") as mock_mgr:
            mock_mgr.return_value.test_connection.side_effect = Exception(
                "[AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'12345']"
            )
            result = probe_adapter_connection("google_ad_manager", self._gam_config())
        assert result.success is False
        assert result.error_code == NETWORK_NOT_FOUND
        assert result.details["gam_fault"]["reason"] == "NETWORK_NOT_FOUND"
        assert result.details["gam_fault"]["trigger"] == "12345"

    def test_not_allowed_surface_through_probe(self):
        with patch("src.adapters.gam.client.GAMClientManager") as mock_mgr:
            mock_mgr.return_value.test_connection.side_effect = Exception("[AuthenticationError.NOT_ALLOWED @ network]")
            result = probe_adapter_connection("google_ad_manager", self._gam_config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert result.details["gam_fault"]["reason"] == "NOT_ALLOWED"

    def test_unhealthy_status_surfaces_through_probe(self):
        """When ``test_connection`` returns UNHEALTHY (rather than raising),
        the message is still classified."""
        from src.adapters.gam.utils.health_check import HealthCheckResult, HealthStatus

        with patch("src.adapters.gam.client.GAMClientManager") as mock_mgr:
            mock_mgr.return_value.test_connection.return_value = HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                check_name="authentication",
                message="Authentication failed: [AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'X']",
                details={},
                duration_ms=0,
            )
            result = probe_adapter_connection("google_ad_manager", self._gam_config())
        assert result.success is False
        assert result.error_code == NETWORK_NOT_FOUND


class TestFreeWheelPreview:
    def test_missing_credentials_returns_inline_error(self):
        preview = preview_adapter("freewheel", {"environment": "production"})
        assert preview.ok is False
        assert preview.error_code == INVALID_CONFIG
        assert "username + password" in (preview.error or "") or "api_token" in (preview.error or "")

    def test_auth_rejection_surfaces_inline(self):
        from src.adapters.freewheel._transport import FreeWheelAuthError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelAuthError("bad", status_code=401)
            preview = preview_adapter("freewheel", {"api_token": "tok"})
        assert preview.ok is False
        assert preview.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in preview.error

    def test_happy_path_surfaces_user_name_as_network_name(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"user_id": 42, "user_name": "alice@example.com"}
            client.inventory.list_sites.return_value = MagicMock()
            preview = preview_adapter("freewheel", {"api_token": "tok"})
        assert preview.ok is True
        assert preview.network_name == "alice@example.com"
        assert preview.inventory_reachable is True

    def test_inventory_unreachable_is_non_fatal(self):
        """If token is valid but inventory probe fails, preview still
        returns ok=True — preview is a soft check, not the provision
        gate. Inventory_reachable flag tells the UI to warn but allow."""
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"user_name": "u"}
            client.inventory.list_sites.side_effect = Exception("scope missing")
            preview = preview_adapter("freewheel", {"api_token": "tok"})
        assert preview.ok is True
        assert preview.inventory_reachable is False


class TestBroadstreetPreview:
    def test_happy_path_returns_network_name(self):
        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.return_value = {"id": "nw1", "name": "Acme Publishers"}
            preview = preview_adapter("broadstreet", {"network_id": "nw1", "api_key": "k"})
        assert preview.ok is True
        assert preview.network_name == "Acme Publishers"
        assert preview.network_code == "nw1"

    def test_wrong_network_classified_as_network_not_found(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("not found", status_code=404)
            preview = preview_adapter("broadstreet", {"network_id": "nw1", "api_key": "k"})
        assert preview.ok is False
        assert preview.error_code == NETWORK_NOT_FOUND
        assert "not found" in preview.error


class TestSpringServePreview:
    def test_happy_path_returns_email_as_network_name(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (200, "[]")
            preview = preview_adapter("springserve", {"email": "ops@pub.com", "password": "x"})
        assert preview.ok is True
        assert preview.network_name == "ops@pub.com"
        assert preview.inventory_reachable is True

    def test_auth_failure_surfaces_inline(self):
        from src.adapters.springserve._transport import SpringServeAuthError

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.side_effect = SpringServeAuthError("bad", status_code=401)
            preview = preview_adapter("springserve", {"api_token": "tok"})
        assert preview.ok is False
        assert preview.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in preview.error
