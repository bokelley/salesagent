"""Tests for src.core.domain_config utility functions."""

from unittest.mock import patch

import pytest

from src.core.domain_config import _resolve_single_tenant_virtual_host, get_sales_agent_domain


@pytest.fixture(autouse=True)
def _clear_vhost_cache():
    """Reset the process-lifetime lru_cache between tests."""
    _resolve_single_tenant_virtual_host.cache_clear()
    yield
    _resolve_single_tenant_virtual_host.cache_clear()


class TestGetSalesAgentDomain:
    """get_sales_agent_domain() resolves the deployment's canonical host."""

    def test_env_var_wins_when_set(self):
        """Explicit SALES_AGENT_DOMAIN env var overrides any derivation."""
        with (
            patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "explicit.example.com"}, clear=True),
            patch("src.core.domain_config._resolve_single_tenant_virtual_host") as mock_resolve,
        ):
            assert get_sales_agent_domain() == "explicit.example.com"
            mock_resolve.assert_not_called()

    def test_single_tenant_virtual_host_fallback(self):
        """Without env var, single-tenant deployments use the tenant's virtual_host."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("src.core.domain_config._resolve_single_tenant_virtual_host") as mock_resolve,
        ):
            mock_resolve.return_value = "agent.mamamia.com.au"
            assert get_sales_agent_domain() == "agent.mamamia.com.au"

    def test_multi_tenant_does_not_fall_back(self):
        """ADCP_MULTI_TENANT=true short-circuits inside the resolver."""
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "true"}, clear=True):
            # Real resolver runs and returns None because of the multi-tenant check.
            # Hits the early-return path before any DB code.
            assert get_sales_agent_domain() is None

    def test_resolver_short_circuit_in_multi_tenant(self):
        """The resolver short-circuits before any DB code when multi-tenant."""
        with (
            patch.dict("os.environ", {"ADCP_MULTI_TENANT": "true"}, clear=True),
            patch("src.core.database.database_session.get_engine") as mock_engine,
        ):
            assert _resolve_single_tenant_virtual_host() is None
            # Multi-tenant gate must return before touching the engine.
            mock_engine.assert_not_called()

    def test_db_failure_returns_none(self):
        """If the DB lookup raises (e.g., during early startup), return None."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("src.core.database.database_session.get_engine") as mock_engine,
        ):
            mock_engine.side_effect = RuntimeError("db not ready")
            assert get_sales_agent_domain() is None
