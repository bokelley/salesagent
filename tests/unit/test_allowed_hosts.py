"""Unit tests for ``core.main._allowed_hosts``.

Covers the FastMCP DNS-rebinding allowlist that enumerates dev tenant
subdomains, including the ``DEV_TENANT_SUBDOMAINS`` env-var override
introduced in #32.
"""

from __future__ import annotations

from unittest.mock import patch

from core.main import DEFAULT_DEV_TENANT_SUBDOMAINS, _allowed_hosts


class TestAllowedHostsDefaults:
    """With no env override, the function returns the baked-in tenant list."""

    def test_includes_loopback_aliases(self):
        with patch.dict("os.environ", {}, clear=False) as _env:
            _env.pop("DEV_TENANT_SUBDOMAINS", None)
            hosts = _allowed_hosts()
        assert "localhost" in hosts
        assert "127.0.0.1" in hosts
        assert "0.0.0.0" in hosts
        assert "localtest.me" in hosts

    def test_includes_default_tenants_under_both_aliases(self):
        with patch.dict("os.environ", {}, clear=False) as _env:
            _env.pop("DEV_TENANT_SUBDOMAINS", None)
            hosts = _allowed_hosts()
        for tenant in DEFAULT_DEV_TENANT_SUBDOMAINS:
            assert f"{tenant}.localhost" in hosts
            assert f"{tenant}.localtest.me" in hosts


class TestAllowedHostsEnvOverride:
    """``DEV_TENANT_SUBDOMAINS`` env var replaces the baked-in list."""

    def test_env_override_replaces_default_list(self):
        with patch.dict("os.environ", {"DEV_TENANT_SUBDOMAINS": "alpha,bravo"}):
            hosts = _allowed_hosts()
        assert "alpha.localhost" in hosts
        assert "bravo.localtest.me" in hosts
        # Defaults that aren't in the override are absent
        assert "wonderstruck.localhost" not in hosts
        assert "acme.localtest.me" not in hosts

    def test_env_override_strips_whitespace(self):
        with patch.dict("os.environ", {"DEV_TENANT_SUBDOMAINS": "  alpha , bravo  "}):
            hosts = _allowed_hosts()
        assert "alpha.localhost" in hosts
        assert "bravo.localtest.me" in hosts
        # Whitespace-padded names don't sneak in literally
        assert " alpha .localhost" not in hosts

    def test_empty_env_override_leaves_no_tenant_subdomains(self):
        """Explicit empty value means "no dev tenants" — only loopback survives."""
        with patch.dict("os.environ", {"DEV_TENANT_SUBDOMAINS": ""}):
            hosts = _allowed_hosts()
        assert "localhost" in hosts
        assert "127.0.0.1" in hosts
        # No tenant subdomains
        for tenant in DEFAULT_DEV_TENANT_SUBDOMAINS:
            assert f"{tenant}.localhost" not in hosts

    def test_env_override_skips_blank_entries(self):
        """Trailing commas / empty pieces are ignored."""
        with patch.dict("os.environ", {"DEV_TENANT_SUBDOMAINS": "alpha,,bravo,"}):
            hosts = _allowed_hosts()
        assert "alpha.localhost" in hosts
        assert "bravo.localhost" in hosts
        # No empty-string entry like ``.localhost``
        assert ".localhost" not in hosts
