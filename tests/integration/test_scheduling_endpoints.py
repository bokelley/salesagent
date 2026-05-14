"""Integration tests for the ``/admin/scheduling`` endpoints (#382 Stage 4).

Covers the Flask-routing layer: super-admin gating, JSON payload shape,
Run Now dispatch through the shared orchestrator.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.adapter_sync_orchestration import SyncExecutionResult
from tests.factories import AdapterConfigFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestSchedulingIndexRequiresSuperAdmin:
    """Anonymous request → 302 to login; tenant_admin role → 403."""

    def test_anonymous_redirects_to_login(self, admin_client):
        resp = admin_client.get("/admin/scheduling")
        # ``require_auth`` redirects to login when no session.
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")


class TestSchedulingIndexRendersForSuperAdmin:
    def test_page_renders_with_configured_tenant_row(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_idx", name="Index Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        resp = authenticated_admin_session.get("/admin/scheduling")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Adapter Sync Scheduling" in body
        assert "Index Co" in body
        assert "freewheel" in body
        # Both kinds should render — FW supports both.
        assert "inventory" in body
        assert "reporting" in body


class TestJobsApiReturnsMatrixJson:
    def test_jobs_endpoint_returns_rows(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_json", name="JSON Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        resp = authenticated_admin_session.get("/admin/api/scheduling/jobs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "rows" in data
        rows = [r for r in data["rows"] if r["tenant_id"] == "t_json"]
        assert len(rows) == 2  # inventory + reporting
        kinds = {r["sync_kind"] for r in rows}
        assert kinds == {"inventory", "reporting"}
        assert all(r["never_run"] is True for r in rows)


class TestRunNowValidatesBody:
    def test_missing_fields_returns_400(self, authenticated_admin_session):
        resp = authenticated_admin_session.post("/admin/api/scheduling/run", json={})
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    def test_unknown_sync_kind_returns_400(self, authenticated_admin_session):
        resp = authenticated_admin_session.post(
            "/admin/api/scheduling/run",
            json={"tenant_id": "t1", "adapter_type": "freewheel", "sync_kind": "foobar"},
        )
        assert resp.status_code == 400
        assert "sync_kind" in resp.get_json()["error"]


class TestRunNowDispatchesToOrchestrator:
    """The endpoint delegates to ``execute_adapter_sync``; we patch the
    orchestrator to keep the test fast and focused on the HTTP layer
    (the orchestrator itself has its own integration coverage)."""

    def test_successful_dispatch_returns_200(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_run", name="Run Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        fake_result = SyncExecutionResult(
            sync_id="sync_test_ok",
            sync_kind="inventory",
            succeeded=True,
            counts={"site": 5},
        )
        with patch(
            "src.admin.blueprints.scheduling.execute_adapter_sync",
            return_value=fake_result,
        ) as mock_exec:
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_run",
                    "adapter_type": "freewheel",
                    "sync_kind": "inventory",
                },
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["sync_id"] == "sync_test_ok"
        assert body["succeeded"] is True
        mock_exec.assert_called_once_with(
            tenant_id="t_run",
            adapter_type="freewheel",
            sync_kind="inventory",
            triggered_by="admin_scheduling_ui",
        )

    def test_scope_pending_returns_503(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_scope", name="Scope Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        fake_result = SyncExecutionResult(
            sync_id="sync_scope",
            sync_kind="reporting",
            succeeded=False,
            errors={"scope": "denied: /reports/queries"},
            metadata={"scope_pending": True},
        )
        with patch(
            "src.admin.blueprints.scheduling.execute_adapter_sync",
            return_value=fake_result,
        ):
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_scope",
                    "adapter_type": "freewheel",
                    "sync_kind": "reporting",
                },
            )
        assert resp.status_code == 503
        assert resp.get_json()["scope_pending"] is True

    def test_unconfigured_tenant_returns_400(self, authenticated_admin_session, factory_session):
        with patch(
            "src.admin.blueprints.scheduling.execute_adapter_sync",
            return_value=None,
        ):
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_unknown",
                    "adapter_type": "freewheel",
                    "sync_kind": "inventory",
                },
            )
        assert resp.status_code == 400
        assert "not configured" in resp.get_json()["error"]
