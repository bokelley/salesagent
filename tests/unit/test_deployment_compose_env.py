"""Deployment compose files must not default production-shaped services to dev mode."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_multi_tenant_compose_pins_runtime_services_to_production():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.multi-tenant.yml").read_text(encoding="utf-8"))

    for service_name in ("adcp-server", "admin-ui"):
        environment = compose["services"][service_name]["environment"]
        assert environment["ENVIRONMENT"] == "production"
        assert environment["FLASK_ENV"] == "production"


def test_multi_tenant_admin_ui_does_not_default_to_global_test_auth():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.multi-tenant.yml").read_text(encoding="utf-8"))

    environment = compose["services"]["admin-ui"]["environment"]

    assert environment["ADCP_AUTH_TEST_MODE"] == "${ADCP_AUTH_TEST_MODE:-false}"
