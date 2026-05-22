"""Reverse-proxy smoke tests for sprint-1 endpoints.

The salesagent already supports path-prefix mounts (CLAUDE.md pattern #6:
``request.script_root`` in Python, ``scriptRoot`` in JS). Sprint 1 needs to
verify the new Tenant Management API endpoints work correctly when the app
is mounted under a non-root path prefix — e.g. when Scope3 Storefront proxies
``/storefront/salesagent/`` to the salesagent's root.

We simulate the proxy by setting ``SCRIPT_NAME`` and ``HTTP_X_FORWARDED_PREFIX``
on the WSGI environ. Flask routes still match against the path *after* the
script_name, so endpoints work, and ``request.script_root`` returns the prefix
for templates / fetch URL construction.
"""

from __future__ import annotations

import pytest
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from src.admin.tenant_management_api import tenant_management_api
from tests.helpers.managed_tenant_api import install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-rev-proxy-test-key"
SCRIPT_NAME = "/storefront/salesagent"


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def proxied_app(integration_db, install_api_key):
    """Build a Flask app that simulates being mounted under a path prefix."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tenant_management_api)
    # ProxyFix is what production uses to honour X-Forwarded-* headers from the proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    return app


@pytest.fixture
def proxied_client(proxied_app):
    return proxied_app.test_client()


def test_health_check_works_under_path_prefix(proxied_client, install_api_key):
    """The simplest case: a GET against the health endpoint via the proxy prefix.

    Flask's test_client paths are *after* SCRIPT_NAME — the prefix lives in environ.
    """
    resp = proxied_client.get(
        "/api/v1/tenant-management/health",
        headers={
            "X-Tenant-Management-API-Key": install_api_key,
            "X-Forwarded-Prefix": SCRIPT_NAME,
        },
        environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "healthy"


def test_openapi_spec_loads_under_path_prefix(proxied_client, install_api_key):
    """Swagger / OpenAPI endpoints must work under the path prefix too."""
    resp = proxied_client.get(
        "/api/v1/tenant-management/docs/openapi.json",
        headers={
            "X-Tenant-Management-API-Key": install_api_key,
            "X-Forwarded-Prefix": SCRIPT_NAME,
        },
        environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
    )
    assert resp.status_code == 200
    spec_doc = resp.get_json()
    assert spec_doc.get("openapi", "").startswith("3.")


def test_request_script_root_reflects_prefix(proxied_app, install_api_key):
    """Verify request.script_root carries the prefix so URL helpers can use it."""
    captured: dict[str, str] = {}

    @proxied_app.route("/api/v1/tenant-management/_test_script_root")
    def probe():
        from flask import request

        captured["script_root"] = request.script_root
        captured["path"] = request.path
        return {"ok": True}, 200

    with proxied_app.test_client() as client:
        resp = client.get(
            "/api/v1/tenant-management/_test_script_root",
            headers={"X-Forwarded-Prefix": SCRIPT_NAME},
            environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
        )
        assert resp.status_code == 200

    assert captured["script_root"] == SCRIPT_NAME
    # request.path is relative to script_root, so it must NOT include the prefix.
    assert captured["path"].startswith("/api/v1/tenant-management/")
    assert not captured["path"].startswith(SCRIPT_NAME)
