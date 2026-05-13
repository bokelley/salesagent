"""Regression: SetupChecklistService must not crash on non-admin-UI callers.

The service is invoked from three contexts:

* **Admin UI** (full Flask app) — admin pages render the checklist with real URLs.
* **Tenant Management API** (standalone Flask app, only the
  ``tenant_management_api`` blueprint registered) — ``url_for`` for an
  admin-UI endpoint raises ``werkzeug.routing.BuildError`` because that
  endpoint isn't registered in the API's app.
* **MCP / A2A** — ``validate_setup_complete`` runs inside
  ``_create_media_buy_impl`` (transport-agnostic business logic served by
  Starlette via ``adcp.server.serve``). No Flask request stack exists.

Before this guard, ``_settings_url`` / ``_route_url`` eagerly called
``flask.url_for`` during ``SetupTask`` construction, which raises
``RuntimeError`` on the MCP/A2A path and ``BuildError`` on the management
API path — every production ``create_media_buy`` call would 500 the moment
the setup gate ran, and the management API's ``/status`` endpoint would
500 for any tenant. See issue #357 follow-up.

These tests pin the contract: the service builds without crashing on
either non-admin-UI path, ``action_url`` is ``None``, and the completion
gate still evaluates correctly so callers like ``validate_setup_complete``
keep working.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from src.services.setup_checklist_service import SetupChecklistService


def _tenant(tenant_id: str = "t1") -> SimpleNamespace:
    """Stand-in Tenant with the minimum surface the AAO gate reads."""
    return SimpleNamespace(
        tenant_id=tenant_id,
        public_agent_url=None,
        virtual_host=None,
        subdomain="t1",
        is_embedded=False,
    )


class TestServiceWorksWithoutFlaskContext:
    """MCP/A2A path: no Flask request stack at all (``RuntimeError``)."""

    def test_settings_url_returns_none_outside_request_context(self):
        url = SetupChecklistService("t1")._settings_url("account")
        assert url is None

    def test_route_url_returns_none_outside_request_context(self):
        url = SetupChecklistService("t1")._route_url("users.list_users")
        assert url is None

    def test_build_aao_tasks_does_not_crash_outside_request_context(self):
        """The validator path constructs tasks but only reads ``name``/``is_complete``.
        It must not crash on URL construction."""
        tasks = SetupChecklistService("t1")._build_aao_tasks(_tenant())
        assert len(tasks) == 1
        task = tasks[0]
        # Completion gate still works — that's the load-bearing part for
        # validate_setup_complete; the URL is cosmetic.
        assert task.is_complete is False
        assert task.name  # name is what the SetupIncompleteError message uses
        # No URL available without a request context — but no exception either.
        assert task.action_url is None


class TestServiceWorksInForeignFlaskApp:
    """Tenant Management API path: Flask context exists but admin-UI
    endpoints aren't registered → ``BuildError``, not ``RuntimeError``."""

    @pytest.fixture
    def foreign_app_context(self):
        """Flask app with no admin blueprints — mirrors the management
        API's bare app in ``tests/integration/test_managed_tenant_api.py``."""
        app = Flask(__name__)
        with app.test_request_context():
            yield

    def test_settings_url_returns_none_when_endpoint_unregistered(self, foreign_app_context):
        url = SetupChecklistService("t1")._settings_url("account")
        assert url is None

    def test_route_url_returns_none_when_endpoint_unregistered(self, foreign_app_context):
        url = SetupChecklistService("t1")._route_url("users.list_users")
        assert url is None

    def test_build_aao_tasks_does_not_crash_in_foreign_app(self, foreign_app_context):
        tasks = SetupChecklistService("t1")._build_aao_tasks(_tenant())
        assert tasks[0].action_url is None
        assert tasks[0].is_complete is False
