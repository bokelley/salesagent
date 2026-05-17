"""Tests for src.core.domain_config utility functions."""

from unittest.mock import patch

from src.core.domain_config import get_sales_agent_domain


class TestGetSalesAgentDomain:
    """get_sales_agent_domain() resolves the deployment's canonical host."""

    def test_env_var_wins_when_set(self):
        """Explicit SALES_AGENT_DOMAIN env var overrides any derivation."""
        with (
            patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "explicit.example.com"}),
            patch("src.core.config_loader.get_single_tenant") as mock_get_single,
        ):
            mock_get_single.return_value = {"virtual_host": "should-not-be-used.example.com"}
            assert get_sales_agent_domain() == "explicit.example.com"
            mock_get_single.assert_not_called()

    def test_single_tenant_virtual_host_fallback(self):
        """Without env var, single-tenant deployments use the tenant's virtual_host."""
        with (
            patch.dict("os.environ", {}, clear=False),
            patch("src.core.config_loader.get_single_tenant") as mock_get_single,
        ):
            # Ensure no env var leak from outer environment.
            with patch.dict("os.environ", {}, clear=True):
                mock_get_single.return_value = {"virtual_host": "agent.mamamia.com.au"}
                assert get_sales_agent_domain() == "agent.mamamia.com.au"

    def test_multi_tenant_does_not_fall_back(self):
        """In multi-tenant mode get_single_tenant returns None — no fallback."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("src.core.config_loader.get_single_tenant") as mock_get_single,
        ):
            # Mirrors get_single_tenant's behavior when ADCP_MULTI_TENANT=true.
            mock_get_single.return_value = None
            assert get_sales_agent_domain() is None

    def test_single_tenant_without_virtual_host_returns_none(self):
        """Single-tenant tenant exists but has no virtual_host configured."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("src.core.config_loader.get_single_tenant") as mock_get_single,
        ):
            mock_get_single.return_value = {"virtual_host": None, "tenant_id": "default"}
            assert get_sales_agent_domain() is None

    def test_db_failure_returns_none(self):
        """If the DB lookup raises (e.g., during early startup), return None."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("src.core.config_loader.get_single_tenant") as mock_get_single,
        ):
            mock_get_single.side_effect = RuntimeError("db not ready")
            assert get_sales_agent_domain() is None
