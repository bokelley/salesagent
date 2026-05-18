"""Integration tests for Sprint 3 Tenant Management API endpoints.

Covers 8 Tenant Management API endpoints:

- workflows: list / detail / approve / reject (idempotency + 409 conflict cases)
- media-buys: list / detail (read-only)
- audit-log: filterable list with cursor pagination
- sync-history: historical timeline

Each endpoint covers the happy path + 401 (missing key) + 404 (unknown id)
plus the idempotent / 409 conflict behaviors required by the spec.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from src.admin.tenant_management_api import tenant_management_api
from tests.factories import (
    AuditLogFactory,
    ContextFactory,
    MediaBuyFactory,
    ObjectWorkflowMappingFactory,
    PrincipalFactory,
    SyncJobFactory,
    TenantFactory,
    WorkflowStepFactory,
)
from tests.helpers.managed_tenant_api import bind_factories_to_session, install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-sprint-3-test-key"


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def app(integration_db, install_api_key):
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture
def bound_factories(integration_db):
    """Bind factories to a session — see test_managed_tenant_api.py for context."""
    with bind_factories_to_session() as session:
        yield session


@pytest.fixture
def tenant(bound_factories):
    """Tenant scoped to one test."""
    return TenantFactory()


@pytest.fixture
def other_tenant(bound_factories):
    """Second tenant — used for cross-tenant isolation checks."""
    return TenantFactory()


@pytest.fixture
def principal(bound_factories, tenant):
    return PrincipalFactory(tenant=tenant)


@pytest.fixture
def workflow_step(bound_factories, tenant, principal):
    """Pending workflow step gating a media buy."""
    ctx = ContextFactory(tenant=tenant, principal=principal)
    step = WorkflowStepFactory(context=ctx, status="pending", tool_name="create_media_buy")
    ObjectWorkflowMappingFactory(workflow_step=step, object_type="media_buy", object_id="mb_subject")
    return step


# ---------------------------------------------------------------------------
# Auth (401 cases) — applied across all endpoints via a shared parametrize
# ---------------------------------------------------------------------------


class TestAuthRequired:
    """Every Sprint 3 endpoint requires the management API key."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/tenant-management/tenants/t1/workflows"),
            ("GET", "/api/v1/tenant-management/tenants/t1/workflows/w1"),
            ("POST", "/api/v1/tenant-management/tenants/t1/workflows/w1/approve"),
            ("POST", "/api/v1/tenant-management/tenants/t1/workflows/w1/reject"),
            ("GET", "/api/v1/tenant-management/tenants/t1/media-buys"),
            ("GET", "/api/v1/tenant-management/tenants/t1/media-buys/mb1"),
            ("GET", "/api/v1/tenant-management/tenants/t1/audit-log"),
            ("GET", "/api/v1/tenant-management/tenants/t1/sync-history"),
        ],
    )
    def test_missing_api_key_returns_401(self, client, method, path):
        response = client.open(
            path,
            method=method,
            json={"notes": "x"} if method == "POST" else None,
        )
        assert response.status_code == 401, response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Workflow list
# ---------------------------------------------------------------------------


class TestListWorkflows:
    def test_404_when_tenant_unknown(self, client, auth_headers):
        response = client.get(
            "/api/v1/tenant-management/tenants/tenant_does_not_exist/workflows",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.get_json()["error"] == "tenant_not_found"

    def test_lists_pending_workflows_first(self, client, auth_headers, tenant, principal, bound_factories):
        tenant_id = tenant.tenant_id
        ctx = ContextFactory(tenant=tenant, principal=principal)
        decided = WorkflowStepFactory(
            context=ctx,
            status="completed",
            response_data={"decision": "approve"},
        )
        decided_id = decided.step_id
        ObjectWorkflowMappingFactory(workflow_step=decided, object_type="media_buy", object_id="mb_decided")
        pending = WorkflowStepFactory(context=ctx, status="pending")
        pending_id = pending.step_id
        ObjectWorkflowMappingFactory(workflow_step=pending, object_type="media_buy", object_id="mb_pending")

        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/workflows",
            headers=auth_headers,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        ids = [w["workflow_id"] for w in body["workflows"]]
        assert pending_id in ids
        assert decided_id in ids
        assert ids.index(pending_id) < ids.index(decided_id)
        pending_summary = next(w for w in body["workflows"] if w["workflow_id"] == pending_id)
        assert pending_summary["status"] == "pending"
        approved_summary = next(w for w in body["workflows"] if w["workflow_id"] == decided_id)
        assert approved_summary["status"] == "approved"

    def test_filter_by_status(self, client, auth_headers, tenant, principal, bound_factories):
        tenant_id = tenant.tenant_id
        ctx = ContextFactory(tenant=tenant, principal=principal)
        pending = WorkflowStepFactory(context=ctx, status="pending")
        pending_id = pending.step_id
        ObjectWorkflowMappingFactory(workflow_step=pending, object_type="media_buy", object_id="mb_p")
        approved = WorkflowStepFactory(
            context=ctx,
            status="completed",
            response_data={"decision": "approve"},
        )
        approved_id = approved.step_id
        ObjectWorkflowMappingFactory(workflow_step=approved, object_type="media_buy", object_id="mb_a")

        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/workflows?status=pending",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        ids = {w["workflow_id"] for w in body["workflows"]}
        assert pending_id in ids
        assert approved_id not in ids

    def test_cross_tenant_isolation(self, client, auth_headers, tenant, other_tenant, bound_factories):
        tenant_id = tenant.tenant_id
        my_principal = PrincipalFactory(tenant=tenant)
        other_principal = PrincipalFactory(tenant=other_tenant)
        my_ctx = ContextFactory(tenant=tenant, principal=my_principal)
        other_ctx = ContextFactory(tenant=other_tenant, principal=other_principal)
        my_step = WorkflowStepFactory(context=my_ctx)
        my_step_id = my_step.step_id
        ObjectWorkflowMappingFactory(workflow_step=my_step, object_id="mb_mine")
        other_step = WorkflowStepFactory(context=other_ctx)
        other_step_id = other_step.step_id
        ObjectWorkflowMappingFactory(workflow_step=other_step, object_id="mb_other")

        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/workflows",
            headers=auth_headers,
        )
        ids = {w["workflow_id"] for w in response.get_json()["workflows"]}
        assert my_step_id in ids
        assert other_step_id not in ids


# ---------------------------------------------------------------------------
# Workflow detail
# ---------------------------------------------------------------------------


class TestGetWorkflow:
    def test_happy_path(self, client, auth_headers, tenant, workflow_step):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["workflow_id"] == workflow_step.step_id
        assert body["status"] == "pending"
        assert body["subject_type"] == "media_buy"
        assert body["subject_id"] == "mb_subject"
        assert body["decisions"] == []

    def test_404_unknown_workflow(self, client, auth_headers, tenant):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/no_such_step",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.get_json()["error"] == "workflow_not_found"

    def test_404_wrong_tenant(self, client, auth_headers, tenant, other_tenant, workflow_step):
        # The step belongs to ``tenant``; querying it under ``other_tenant`` 404s.
        response = client.get(
            f"/api/v1/tenant-management/tenants/{other_tenant.tenant_id}/workflows/{workflow_step.step_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Workflow approve / reject
# ---------------------------------------------------------------------------


class TestApproveWorkflow:
    def test_approve_pending_workflow(self, client, auth_headers, tenant, workflow_step):
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=auth_headers,
            json={"notes": "looks good"},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["status"] == "approved"
        assert len(body["decisions"]) == 1
        decision = body["decisions"][0]
        assert decision["decision"] == "approve"
        assert decision["notes"] == "looks good"
        # No identity headers → recorded as management_api with no email.
        assert decision["decided_by_source"] == "management_api"
        assert decision["decided_by_email"] is None

    def test_approve_records_identity_when_propagated(self, client, auth_headers, tenant, workflow_step):
        headers = {
            **auth_headers,
            "X-Identity-Email": "alice@host.example",
            "X-Identity-Org-Id": "org_acme",
            "X-Identity-Role": "admin",
            "X-Identity-Source": "scope3_storefront",
        }
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=headers,
            json={},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        decision = response.get_json()["decisions"][0]
        assert decision["decided_by_email"] == "alice@host.example"
        assert decision["decided_by_source"] == "scope3_storefront"

    def test_idempotent_re_approve_returns_200_existing_state(self, client, auth_headers, tenant, workflow_step):
        first = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=auth_headers,
            json={"notes": "first"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=auth_headers,
            json={"notes": "second"},
        )
        # Idempotent: returns existing state, not a new decision.
        assert second.status_code == 200
        assert len(second.get_json()["decisions"]) == 1
        assert second.get_json()["decisions"][0]["notes"] == "first"

    def test_409_when_already_rejected(self, client, auth_headers, tenant, workflow_step):
        reject_resp = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/reject",
            headers=auth_headers,
            json={"notes": "nope"},
        )
        assert reject_resp.status_code == 200
        approve_resp = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=auth_headers,
            json={},
        )
        assert approve_resp.status_code == 409
        assert approve_resp.get_json()["error"] == "workflow_already_decided"

    def test_409_when_expired(self, client, auth_headers, tenant, principal, bound_factories):
        ctx = ContextFactory(tenant=tenant, principal=principal)
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        step = WorkflowStepFactory(
            context=ctx,
            status="pending",
            request_data={"expires_at": past, "description": "expired"},
        )
        ObjectWorkflowMappingFactory(workflow_step=step, object_id="mb_x")
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{step.step_id}/approve",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 409
        assert response.get_json()["error"] == "workflow_expired"

    def test_404_unknown_workflow(self, client, auth_headers, tenant):
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/missing/approve",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 404
        assert response.get_json()["error"] == "workflow_not_found"


class TestRejectWorkflow:
    def test_reject_requires_notes(self, client, auth_headers, tenant, workflow_step):
        # Pydantic validation: empty notes rejected at schema layer (422 from spectree).
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/reject",
            headers=auth_headers,
            json={"notes": ""},
        )
        assert response.status_code in (400, 422)

    def test_reject_pending_workflow(self, client, auth_headers, tenant, workflow_step):
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/reject",
            headers=auth_headers,
            json={"notes": "policy violation"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "rejected"
        assert body["decisions"][0]["decision"] == "reject"
        assert body["decisions"][0]["notes"] == "policy violation"

    def test_409_after_approve(self, client, auth_headers, tenant, workflow_step):
        approve_resp = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=auth_headers,
            json={},
        )
        assert approve_resp.status_code == 200
        reject_resp = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/reject",
            headers=auth_headers,
            json={"notes": "changed mind"},
        )
        assert reject_resp.status_code == 409


class TestApproveInvalidatesStatusCache:
    def test_status_cache_drops_after_approve(self, client, auth_headers, tenant, workflow_step):
        from src.admin.services import tenant_status_service

        # Prime the cache with a known snapshot so we can verify it was dropped.
        tenant_status_service._CACHE[tenant.tenant_id] = (9.99e9, object())  # type: ignore[assignment]

        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{workflow_step.step_id}/approve",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 200
        # invalidate_status_cache(tid) is called from the approve handler.
        assert tenant.tenant_id not in tenant_status_service._CACHE


# ---------------------------------------------------------------------------
# Media-buy list / detail
# ---------------------------------------------------------------------------


class TestListMediaBuys:
    def test_404_unknown_tenant(self, client, auth_headers):
        response = client.get(
            "/api/v1/tenant-management/tenants/tenant_missing/media-buys",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_lists_buys_with_principal_name(self, client, auth_headers, tenant, principal, bound_factories):
        tenant_id = tenant.tenant_id
        principal_name = principal.name
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        buy_id = buy.media_buy_id
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/media-buys",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        ids = [b["media_buy_id"] for b in body["media_buys"]]
        assert buy_id in ids
        entry = next(b for b in body["media_buys"] if b["media_buy_id"] == buy_id)
        assert entry["principal_name"] == principal_name
        # Buy hasn't actually started (start_date in the future per factory)
        # OR has no delivery data, so pacing must be None per spec.
        assert entry["pacing"] is None

    def test_filter_by_status(self, client, auth_headers, tenant, principal, bound_factories):
        tenant_id = tenant.tenant_id
        active = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        active_id = active.media_buy_id
        pending = MediaBuyFactory(tenant=tenant, principal=principal, status="pending_approval")
        pending_id = pending.media_buy_id
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/media-buys?status=active",
            headers=auth_headers,
        )
        ids = {b["media_buy_id"] for b in response.get_json()["media_buys"]}
        assert active_id in ids
        assert pending_id not in ids

    def test_cross_tenant_isolation(self, client, auth_headers, tenant, other_tenant, bound_factories):
        tenant_id = tenant.tenant_id
        my_principal = PrincipalFactory(tenant=tenant)
        other_principal = PrincipalFactory(tenant=other_tenant)
        mine = MediaBuyFactory(tenant=tenant, principal=my_principal)
        mine_id = mine.media_buy_id
        other = MediaBuyFactory(tenant=other_tenant, principal=other_principal)
        other_id = other.media_buy_id
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/media-buys",
            headers=auth_headers,
        )
        ids = {b["media_buy_id"] for b in response.get_json()["media_buys"]}
        assert mine_id in ids
        assert other_id not in ids


class TestGetMediaBuy:
    def test_happy_path(self, client, auth_headers, tenant, principal, bound_factories):
        tenant_id = tenant.tenant_id
        principal_name = principal.name
        buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            raw_request={
                "buyer_ref": "ref_123",
                "packages": [{"package_id": "pkg_1", "product_id": "prod_alpha"}],
            },
        )
        buy_id = buy.media_buy_id
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/media-buys/{buy_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["media_buy_id"] == buy_id
        assert body["buyer_ref"] == "ref_123"
        assert body["products"] == ["prod_alpha"]
        assert body["principal_name"] == principal_name

    def test_404_when_unknown(self, client, auth_headers, tenant):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/media-buys/no_such_buy",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.get_json()["error"] == "media_buy_not_found"

    def test_no_write_methods_exposed(self, client, auth_headers, tenant, principal, bound_factories):
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        # No POST/PATCH/DELETE on detail or collection.
        for method in ("POST", "PATCH", "DELETE"):
            r = client.open(
                f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/media-buys/{buy.media_buy_id}",
                method=method,
                headers=auth_headers,
                json={},
            )
            # 405 = method not allowed (route exists for GET only).
            # 404 = blueprint never registered the route at all.
            # Both are acceptable evidence that no write path is exposed.
            assert r.status_code in (404, 405), (method, r.status_code)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_404_unknown_tenant(self, client, auth_headers):
        response = client.get(
            "/api/v1/tenant-management/tenants/tenant_missing/audit-log",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_action_prefix_filter(self, client, auth_headers, tenant, bound_factories):
        AuditLogFactory(tenant=tenant, operation="workflow.approve")
        AuditLogFactory(tenant=tenant, operation="workflow.reject")
        AuditLogFactory(tenant=tenant, operation="tenant.update")

        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/audit-log?action_prefix=workflow.",
            headers=auth_headers,
        )
        assert response.status_code == 200
        actions = {e["action"] for e in response.get_json()["entries"]}
        assert "workflow.approve" in actions
        assert "workflow.reject" in actions
        assert "tenant.update" not in actions

    def test_filters_by_external_source(self, client, auth_headers, tenant, bound_factories):
        AuditLogFactory(tenant=tenant, operation="x.evt", external_source="scope3_storefront")
        AuditLogFactory(tenant=tenant, operation="x.evt", external_source="other_host")
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/audit-log?external_source=scope3_storefront",
            headers=auth_headers,
        )
        sources = {e["external_source"] for e in response.get_json()["entries"]}
        assert sources == {"scope3_storefront"}

    def test_cursor_pagination_does_not_skip_or_duplicate(self, client, auth_headers, tenant, bound_factories):
        # Create 5 audit rows with deterministic content.
        rows = [AuditLogFactory(tenant=tenant, operation=f"evt.{i:02d}") for i in range(5)]

        first = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/audit-log?limit=2",
            headers=auth_headers,
        )
        assert first.status_code == 200
        first_body = first.get_json()
        assert len(first_body["entries"]) == 2
        assert first_body["next_cursor"] is not None
        first_ids = [e["audit_log_id"] for e in first_body["entries"]]

        second = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/audit-log?limit=2&cursor={first_body['next_cursor']}",
            headers=auth_headers,
        )
        second_body = second.get_json()
        second_ids = [e["audit_log_id"] for e in second_body["entries"]]
        # No overlap, no duplicates between page 1 and page 2.
        assert set(first_ids).isdisjoint(set(second_ids))
        # Combined first two pages + last page = 5 rows total.
        third = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/audit-log?limit=2&cursor={second_body['next_cursor']}",
            headers=auth_headers,
        )
        third_body = third.get_json()
        all_ids = first_ids + second_ids + [e["audit_log_id"] for e in third_body["entries"]]
        assert len(all_ids) == len(rows)
        assert len(set(all_ids)) == len(rows)


# ---------------------------------------------------------------------------
# Sync history
# ---------------------------------------------------------------------------


class TestSyncHistory:
    def test_404_unknown_tenant(self, client, auth_headers):
        response = client.get(
            "/api/v1/tenant-management/tenants/tenant_missing/sync-history",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_lists_runs_started_at_desc(self, client, auth_headers, tenant, bound_factories):
        tenant_id = tenant.tenant_id
        now = datetime.now(UTC)
        old = SyncJobFactory(tenant=tenant, started_at=now - timedelta(hours=2), status="completed")
        old_id = old.sync_id
        new = SyncJobFactory(tenant=tenant, started_at=now, status="completed")
        new_id = new.sync_id
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/sync-history",
            headers=auth_headers,
        )
        body = response.get_json()
        ids = [r["sync_id"] for r in body["runs"]]
        assert ids.index(new_id) < ids.index(old_id)
        # Wire-side maps DB ``completed`` to ``success``.
        assert all(r["status"] == "success" for r in body["runs"])

    def test_filter_by_sync_type(self, client, auth_headers, tenant, bound_factories):
        SyncJobFactory(tenant=tenant, sync_type="inventory")
        SyncJobFactory(tenant=tenant, sync_type="advertisers")
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/sync-history?sync_type=inventory",
            headers=auth_headers,
        )
        types = {r["sync_type"] for r in response.get_json()["runs"]}
        assert types == {"inventory"}
