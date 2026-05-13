"""Regression: SetupChecklistService must not crash without a Flask request context.

The service is invoked from two transports:

* **Admin UI** (Flask) — admin pages render the checklist with real URLs.
* **MCP / A2A** — ``validate_setup_complete`` runs inside
  ``_create_media_buy_impl`` (transport-agnostic business logic served by
  Starlette via ``adcp.server.serve``). No Flask request stack exists there.

Before this guard, ``_settings_url`` / ``_route_url`` eagerly called
``flask.url_for`` during ``SetupTask`` construction, which raises
``RuntimeError: Working outside of application context`` on the MCP/A2A
path — every production ``create_media_buy`` call would 500 the moment
the setup gate ran. See issue #357 follow-up.

These tests pin the contract: the service builds without crashing
outside a Flask context, ``action_url`` is ``None`` on that path, and
the completion gate still evaluates correctly so callers like
``validate_setup_complete`` keep working.
"""

from __future__ import annotations

from types import SimpleNamespace

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
    """Service runs from non-Flask transports (MCP/A2A); ``url_for`` is unavailable there."""

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
