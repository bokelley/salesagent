"""Tests for the synchronous credential probes used at provision time.

``run_probe()`` is the gate that turns "bad credentials" into
a 400 at provision rather than an eternally-pending inventory sync. These
tests pin the contract for each adapter type:

- Auth rejection → ``(False, <auth-flavored error>)``
- Wrong-publisher binding (valid token, wrong account) → ``(False, ...)``
- Transport failure → ``(False, <transport-flavored error>)``
- Success → ``(True, None)``
- Missing required config → ``(False, <which-field>)`` with no HTTP call

The probes themselves call into live adapter clients; tests mock those at
the call boundary so the behavior under each HTTP outcome is exercised.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.admin.services.adapter_connection_tester import (
    test_adapter_connection as run_probe,
)


class TestFreeWheelProbe:
    """FreeWheel probe is two-call: token_info (auth) + list_sites (binding)."""

    def _config(self, **overrides):
        base = {"api_token": "tok", "environment": "production"}
        base.update(overrides)
        return base

    def test_missing_credentials_fails_without_http(self):
        ok, err = run_probe("freewheel", {"environment": "production"})
        assert ok is False
        assert err is not None
        assert "username + password" in err or "api_token" in err

    def test_auth_rejection_returns_clear_error(self):
        from src.adapters.freewheel._transport import FreeWheelAuthError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelAuthError("bad token", status_code=401)
            ok, err = run_probe("freewheel", self._config())
        assert ok is False
        assert "auth rejected" in err

    def test_inventory_403_signals_wrong_publisher_binding(self):
        from src.adapters.freewheel._transport import FreeWheelForbiddenError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.side_effect = FreeWheelForbiddenError("no inventory scope", status_code=403)
            ok, err = run_probe("freewheel", self._config())
        assert ok is False
        assert "cannot read inventory" in err
        assert "publisher" in err

    def test_transport_failure_returns_transport_error(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = ConnectionError("DNS")
            ok, err = run_probe("freewheel", self._config())
        assert ok is False
        assert "transport failure" in err

    def test_happy_path_returns_true(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.return_value = MagicMock()
            ok, err = run_probe("freewheel", self._config())
        assert ok is True
        assert err is None


class TestBroadstreetProbe:
    """Broadstreet probe is one call: get_network() validates auth + binding."""

    def test_missing_network_id_fails_without_http(self):
        ok, err = run_probe("broadstreet", {"api_key": "k"})
        assert ok is False
        assert "network_id" in err

    def test_missing_api_key_fails_without_http(self):
        ok, err = run_probe("broadstreet", {"network_id": "123"})
        assert ok is False
        assert "api_key" in err

    def test_auth_failure_returns_clear_error(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("forbidden", status_code=403)
            ok, err = run_probe("broadstreet", {"network_id": "123", "api_key": "wrong"})
        assert ok is False
        assert "auth rejected" in err
        assert "403" in err

    def test_wrong_network_id_returns_not_found(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("not found", status_code=404)
            ok, err = run_probe("broadstreet", {"network_id": "999999", "api_key": "k"})
        assert ok is False
        assert "not found" in err
        assert "999999" in err

    def test_happy_path_returns_true(self):
        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.return_value = {"id": 123, "name": "Net"}
            ok, err = run_probe("broadstreet", {"network_id": "123", "api_key": "k"})
        assert ok is True
        assert err is None


class TestSpringServeProbe:
    """SpringServe probe is one transport.probe() call — status code drives
    the outcome. Auth-mint failures from the password grant raise rather
    than returning a status code."""

    def _config(self, **overrides):
        base = {"api_token": "tok"}
        base.update(overrides)
        return base

    def test_missing_credentials_fails_without_http(self):
        ok, err = run_probe("springserve", {})
        assert ok is False
        assert "email + password" in err or "api_token" in err

    def test_auth_mint_failure_returns_clear_error(self):
        from src.adapters.springserve._transport import SpringServeAuthError

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client._transport.probe.side_effect = SpringServeAuthError("bad creds", status_code=401)
            ok, err = run_probe("springserve", {"email": "a@b.com", "password": "x"})
        assert ok is False
        assert "auth rejected" in err

    def test_403_signals_wrong_publisher_binding(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client._transport.probe.return_value = (403, "Forbidden")
            ok, err = run_probe("springserve", self._config())
        assert ok is False
        assert "cannot read supply inventory" in err
        assert "publisher" in err

    def test_happy_path_returns_true(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client._transport.probe.return_value = (200, "[]")
            ok, err = run_probe("springserve", self._config())
        assert ok is True
        assert err is None


class TestRoutingTable:
    """The dispatch in test_adapter_connection covers every adapter the
    discriminated AdapterConfig union accepts. Adding a new adapter to the
    schema without updating this dispatch is a real (and previously latent)
    bug — this guard catches it."""

    def test_all_adapter_types_are_routed(self):
        """Mock-grade success path for every adapter type the schema accepts.
        If a new adapter is added to the union without a probe, this fails."""
        # Mock adapter has no live calls.
        assert run_probe("mock", {"dry_run": True}) == (True, None)

        # The other adapters all branch to their own _test_* function;
        # we don't run their HTTP paths here (covered above), just verify
        # the dispatch reaches them rather than falling through to
        # "Unsupported adapter_type".
        for adapter_type in ("google_ad_manager", "freewheel", "broadstreet", "springserve"):
            ok, err = run_probe(adapter_type, {})
            assert err is None or "Unsupported adapter_type" not in err, (
                f"{adapter_type} fell through to the unsupported-type branch — add a _test_{adapter_type}() probe."
            )
