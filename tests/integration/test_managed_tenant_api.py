"""Integration tests for the sprint-1 Tenant Management API endpoints.

Covers the full sprint-1 acceptance criteria:
- provision happy path + adapter-test failure rollback + duplicate org id
- list / get / patch / deactivate / reactivate / delete (soft + hard)
- adapter-config GET / PUT (with rollback on connection failure) / test-connection
- write-guard behavior (managed vs unmanaged, super-admin override)
- end-to-end: provision → patch → ui-handler-blocks → deactivate → re-provision-blocked
- swagger UI loads, OpenAPI spec validates as OpenAPI 3
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from flask import Flask
from sqlalchemy import select

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.database_session import get_db_session
from src.core.database.managed_tenant_guard import ManagedTenantWriteError
from src.core.database.models import (
    AdapterConfig,
    Creative,
    CurrencyLimit,
    MediaBuy,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from tests.factories import MediaBuyFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.helpers.managed_tenant_api import bind_factories_to_session, install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-managed-tenant-test-key"


@pytest.fixture
def install_api_key(integration_db):
    """Provision the management API key in the test DB."""
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


@pytest.fixture(autouse=True)
def _stub_adapter_test(monkeypatch, request):
    """Default adapter probe to success — individual tests opt into failures via this fixture."""
    if "real_adapter_test" in request.keywords:
        return

    def _stub(adapter_type, config):
        return True, None

    import src.admin.tenant_management_api as api_module

    monkeypatch.setattr(api_module, "test_adapter_connection", _stub)


@pytest.fixture
def bound_factories(integration_db):
    """Bind every factory to a session so tests can call ``XFactory(...)`` and have it persist.

    Delegates to ``bind_factories_to_session()`` — keeps the architecture guard happy
    (no inline session.add() in test bodies) without duplicating the binding logic.
    """
    with bind_factories_to_session() as session:
        yield session


@pytest.fixture
def cleanup_tenants():
    """Clean up tenants created during the test."""
    created: list[str] = []
    yield created
    if not created:
        return
    with get_db_session() as session:
        for tid in created:
            for model in (
                AdapterConfig,
                CurrencyLimit,
                PropertyTag,
                Principal,
                Product,
                Creative,
                MediaBuy,
            ):
                session.execute(model.__table__.delete().where(model.tenant_id == tid))
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _provision_payload(**overrides):
    payload = {
        "name": "Acme News",
        "external_org_id": "org_acme",
        "external_source": "scope3",
        "contact_email": "ops@example.com",
        "adapter": {
            "type": "google_ad_manager",
            "network_code": "12345",
            "service_account_email": "sa@example.com",
            "service_account_key_json": '{"type":"service_account"}',
        },
        "default_currency": "USD",
        "billing_plan": "standard",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------


class TestProvision:
    def test_provision_happy_path_creates_tenant_and_dependencies(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_provision_happy")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["managed_externally"] is True
        assert body["external_org_id"] == "org_provision_happy"
        assert body["adapter"]["type"] == "google_ad_manager"
        assert body["adapter"]["connection_test_passed"] is True

        cleanup_tenants.append(body["tenant_id"])

        # Verify CurrencyLimit + PropertyTag + AdapterConfig were created in the same transaction.
        with get_db_session() as session:
            assert (
                session.scalars(
                    select(CurrencyLimit).filter_by(tenant_id=body["tenant_id"], currency_code="USD")
                ).first()
                is not None
            )
            assert (
                session.scalars(
                    select(PropertyTag).filter_by(tenant_id=body["tenant_id"], tag_id="all_inventory")
                ).first()
                is not None
            )
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=body["tenant_id"])).first()
            assert adapter is not None
            # The encrypted column must round-trip via the property accessor.
            assert adapter.gam_service_account_json == '{"type":"service_account"}'

    def test_provision_with_initial_principal(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(
            external_org_id="org_with_principal",
            initial_principal={"name": "Default Advertiser"},
        )
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201
        body = response.get_json()
        cleanup_tenants.append(body["tenant_id"])
        assert body["initial_principal"]["name"] == "Default Advertiser"
        # Sprint 1 contract: no api_token in response.
        assert "api_token" not in body["initial_principal"]

    def test_provision_rolls_back_on_adapter_failure(self, client, auth_headers, monkeypatch):
        import src.admin.tenant_management_api as api_module

        def _fail(adapter_type, config):
            return False, "auth boom"

        monkeypatch.setattr(api_module, "test_adapter_connection", _fail)

        payload = _provision_payload(external_org_id="org_provision_fail")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"] == "adapter_connection_failed"
        # Verify NOTHING was written.
        with get_db_session() as session:
            assert session.scalars(select(Tenant).filter_by(external_org_id="org_provision_fail")).first() is None

    def test_provision_returns_409_on_duplicate_external_org_id(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_dup")
        first = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert first.status_code == 201
        cleanup_tenants.append(first.get_json()["tenant_id"])

        second = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert second.status_code == 409
        body = second.get_json()
        assert body["error"] == "external_org_id_conflict"
        assert "tenant_id" in body["details"]

    def test_provision_unknown_field_rejected(self, client, auth_headers):
        payload = _provision_payload(external_org_id="org_extra", surprise="oops")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        # spectree returns 422 for Pydantic validation failures.
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Lifecycle: list / get / patch / deactivate / reactivate / delete
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_lifecycle")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tenant_id = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tenant_id)
        return tenant_id

    def test_list_tenants_filters(self, client, auth_headers, managed_tenant):
        resp = client.get(
            "/api/v1/tenant-management/tenants?managed_externally=true&external_source=scope3",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        ids = {t["tenant_id"] for t in body["tenants"]}
        assert managed_tenant in ids
        for t in body["tenants"]:
            assert t["managed_externally"] is True
            assert t["external_source"] == "scope3"

    def test_get_tenant_returns_detail_or_404(self, client, auth_headers, managed_tenant):
        ok = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}", headers=auth_headers)
        assert ok.status_code == 200
        body = ok.get_json()
        assert body["managed_externally"] is True

        missing = client.get("/api/v1/tenant-management/tenants/tenant_nope_404", headers=auth_headers)
        assert missing.status_code == 404
        assert missing.get_json()["error"] == "tenant_not_found"

    def test_patch_updates_platform_managed_fields(self, client, auth_headers, managed_tenant):
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{managed_tenant}",
            headers=auth_headers,
            json={"name": "Renamed Acme", "billing_plan": "enterprise"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["name"] == "Renamed Acme"
        assert body["billing_plan"] == "enterprise"

    def test_patch_rejects_external_org_id(self, client, auth_headers, managed_tenant):
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{managed_tenant}",
            headers=auth_headers,
            json={"external_org_id": "different"},
        )
        assert resp.status_code == 422  # extra="forbid"

    def test_deactivate_then_reactivate_idempotent(self, client, auth_headers, managed_tenant):
        first = client.post(f"/api/v1/tenant-management/tenants/{managed_tenant}/deactivate", headers=auth_headers)
        assert first.status_code == 200
        assert first.get_json()["is_active"] is False

        second = client.post(f"/api/v1/tenant-management/tenants/{managed_tenant}/deactivate", headers=auth_headers)
        assert second.status_code == 200
        assert second.get_json()["is_active"] is False

        re = client.post(f"/api/v1/tenant-management/tenants/{managed_tenant}/reactivate", headers=auth_headers)
        assert re.status_code == 200
        assert re.get_json()["is_active"] is True

    def test_soft_delete_returns_inactive_detail(self, client, auth_headers, managed_tenant):
        resp = client.delete(f"/api/v1/tenant-management/tenants/{managed_tenant}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["is_active"] is False

    def test_hard_delete_requires_confirmation_header(self, client, auth_headers, managed_tenant):
        no_header = client.delete(f"/api/v1/tenant-management/tenants/{managed_tenant}?hard=true", headers=auth_headers)
        assert no_header.status_code == 400
        assert no_header.get_json()["error"] == "confirmation_required"

        with_header = client.delete(
            f"/api/v1/tenant-management/tenants/{managed_tenant}?hard=true",
            headers={**auth_headers, "X-Confirm-Delete": "yes"},
        )
        assert with_header.status_code == 200

        # Tenant should be gone.
        with get_db_session() as session:
            assert session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first() is None

    def test_delete_returns_409_when_active_media_buys_present(
        self, client, auth_headers, managed_tenant, bound_factories
    ):
        # Add a Principal + active MediaBuy to the managed tenant. This goes through the publisher-managed
        # path so the write guard does not fire.
        # Tenant already exists (provisioned by managed_tenant fixture) — load it and pass to factories.
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="p_active_mb",
            name="Has Active",
            platform_mappings={"google_ad_manager": {"advertiser_id": "x"}},
            access_token="t_active_mb",
        )
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_active_test",
            order_name="Active Test",
            advertiser_name="x",
            status="active",
            budget=100,
            start_date=datetime.now(UTC).date(),
            end_date=datetime.now(UTC).date(),
            raw_request={},
        )

        resp = client.delete(f"/api/v1/tenant-management/tenants/{managed_tenant}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "tenant_has_active_resources"


# ---------------------------------------------------------------------------
# Adapter config
# ---------------------------------------------------------------------------


class TestAdapterConfig:
    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_adapter_cfg")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    def test_get_adapter_config_redacts_secrets(self, client, auth_headers, managed_tenant):
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["type"] == "google_ad_manager"
        # The actual secret JSON must NEVER appear in the response — only the redaction marker.
        assert body["service_account_key_json"] == "<encrypted>"
        assert "service_account" not in (body.get("service_account_key_json") or "<encrypted>").replace(
            "<encrypted>", ""
        )

    def test_put_adapter_config_tests_connection_before_commit(self, client, auth_headers, managed_tenant, monkeypatch):
        import src.admin.tenant_management_api as api_module

        def _fail(adapter_type, config):
            return False, "credentials rejected"

        monkeypatch.setattr(api_module, "test_adapter_connection", _fail)

        payload = {
            "type": "google_ad_manager",
            "network_code": "67890",
            "service_account_email": "new@example.com",
            "service_account_key_json": '{"type":"new_sa"}',
        }
        resp = client.put(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config",
            headers=auth_headers,
            json=payload,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "adapter_connection_failed"

        # Existing adapter config unchanged.
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            assert adapter.gam_network_code == "12345"

    def test_put_adapter_config_replaces_existing(self, client, auth_headers, managed_tenant):
        payload = {
            "type": "mock",
        }
        resp = client.put(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config",
            headers=auth_headers,
            json=payload,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["type"] == "mock"

    def test_test_connection_endpoint_does_not_modify_state(self, client, auth_headers, managed_tenant):
        resp = client.post(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config/test-connection",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["error"] is None


# ---------------------------------------------------------------------------
# Write guard
# ---------------------------------------------------------------------------


class TestWriteGuard:
    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_guard")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    @pytest.fixture
    def unmanaged_tenant(self, integration_db, bound_factories):
        TenantFactory(
            tenant_id="t_unmanaged_guard",
            name="Unmanaged",
            subdomain="unmanaged-guard",
            ad_server="mock",
            billing_plan="standard",
            is_active=True,
            managed_externally=False,
        )
        yield "t_unmanaged_guard"
        with get_db_session() as session:
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == "t_unmanaged_guard"))
            session.commit()

    def test_managed_tenant_blocks_non_api_tenant_update(self, managed_tenant):
        # Simulate a UI handler: open a session, mutate a platform-managed field WITHOUT
        # setting the management_api_caller flag — the model guard must fire on commit.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            assert tenant is not None
            tenant.name = "Should Not Persist"
            with pytest.raises(ManagedTenantWriteError):
                session.commit()
            session.rollback()

    def test_unmanaged_tenant_allows_write(self, unmanaged_tenant):
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=unmanaged_tenant)).first()
            tenant.name = "Renamed"
            session.commit()
        with get_db_session() as session:
            assert session.scalars(select(Tenant).filter_by(tenant_id=unmanaged_tenant)).first().name == "Renamed"

    def test_super_admin_override_bypasses_guard(self, managed_tenant):
        with get_db_session() as session:
            session.info["super_admin_override"] = True
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            tenant.name = "Super Admin Rename"
            session.commit()
        with get_db_session() as session:
            assert (
                session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first().name == "Super Admin Rename"
            )

    def test_publisher_managed_table_write_succeeds_on_managed_tenant(self, managed_tenant, bound_factories):
        # Add a Principal directly to the managed tenant, simulating a UI handler.
        # The guard must NOT fire — Principal is publisher-managed.
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        PrincipalFactory(
            tenant=tenant,
            principal_id="p_publisher_write",
            name="Publisher Side",
            platform_mappings={"mock": {"advertiser_id": "x"}},
            access_token="t_pub_write",
        )
        with get_db_session() as session:
            p = session.scalars(
                select(Principal).filter_by(tenant_id=managed_tenant, principal_id="p_publisher_write")
            ).first()
            assert p is not None
            session.delete(p)
            session.commit()

    def test_managed_tenant_blocks_adapter_config_update_outside_api(self, managed_tenant):
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            adapter.gam_network_code = "blocked-rewrite"
            with pytest.raises(ManagedTenantWriteError):
                session.commit()
            session.rollback()


# ---------------------------------------------------------------------------
# End-to-end + reverse-proxy + OpenAPI smoke
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_end_to_end_managed_tenant_lifecycle(self, client, auth_headers, cleanup_tenants, bound_factories):
        # 1) Provision.
        provision_resp = client.post(
            "/api/v1/tenant-management/tenants/provision",
            headers=auth_headers,
            json=_provision_payload(external_org_id="org_e2e"),
        )
        assert provision_resp.status_code == 201
        tenant_id = provision_resp.get_json()["tenant_id"]
        cleanup_tenants.append(tenant_id)

        # 2) Patch via API succeeds.
        patch_resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tenant_id}",
            headers=auth_headers,
            json={"name": "End-to-End Renamed"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["name"] == "End-to-End Renamed"

        # 3) Same write via a UI-style handler (no management_api_caller) → guard fires.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            tenant.name = "UI Should Not Persist"
            with pytest.raises(ManagedTenantWriteError):
                session.commit()
            session.rollback()

        # 4) Adding a Product (publisher-managed) via a UI-style handler succeeds.
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        ProductFactory(
            tenant=tenant,
            product_id="prod_e2e",
            name="E2E Product",
            description="Created without management_api_caller",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            targeting_template={},
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
        )
        with get_db_session() as session:
            assert (
                session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id="prod_e2e")).first()
                is not None
            )

        # 5) Deactivate via API.
        deactivate_resp = client.post(f"/api/v1/tenant-management/tenants/{tenant_id}/deactivate", headers=auth_headers)
        assert deactivate_resp.status_code == 200
        assert deactivate_resp.get_json()["is_active"] is False

        # 6) Re-provision with the same external_org_id → 409.
        repeat = client.post(
            "/api/v1/tenant-management/tenants/provision",
            headers=auth_headers,
            json=_provision_payload(external_org_id="org_e2e"),
        )
        assert repeat.status_code == 409
        assert repeat.get_json()["error"] == "external_org_id_conflict"


class TestOpenAPI:
    def test_swagger_ui_loads(self, client):
        resp = client.get("/api/v1/tenant-management/docs/swagger/")
        assert resp.status_code == 200
        # Swagger UI HTML uses the swagger-ui CSS + JS bundle.
        body = resp.get_data(as_text=True)
        assert "swagger" in body.lower()

    def test_openapi_spec_validates_as_openapi3(self, client):
        resp = client.get("/api/v1/tenant-management/docs/openapi.json")
        assert resp.status_code == 200
        spec_doc = resp.get_json()

        # Minimal OpenAPI 3 sanity
        assert spec_doc.get("openapi", "").startswith("3.")
        assert "info" in spec_doc and "paths" in spec_doc

        # Sprint-1 endpoints must appear in the spec.
        paths = spec_doc["paths"]
        joined = json.dumps(paths)
        assert "/tenants/provision" in joined
        assert "/adapter-config" in joined
        assert "/deactivate" in joined
        assert "/reactivate" in joined
        # Sprint-1.5
        assert "/tenants/preview-adapter" in joined


# ---------------------------------------------------------------------------
# Sprint 1.5: preview-adapter
# ---------------------------------------------------------------------------


@pytest.fixture
def real_adapter_test_disabled(monkeypatch):
    """Stub ``preview_adapter`` to passthrough — used by tests that don't care about adapter type."""
    from src.admin.services.adapter_connection_tester import AdapterPreview
    import src.admin.tenant_management_api as api_module

    def _stub(adapter_type, cfg):
        if adapter_type == "mock":
            return AdapterPreview(
                ok=True,
                network_name="Mock Network",
                network_code="mock",
                currency_code="USD",
                time_zone="UTC",
                inventory_reachable=True,
            )
        return AdapterPreview(ok=True, network_code=str(cfg.get("network_code") or ""))

    monkeypatch.setattr(api_module, "preview_adapter", _stub)


class TestPreviewAdapter:
    """``POST /tenants/preview-adapter`` — pre-provision probe with no persistence.

    Bad creds return ``200 + ok=false`` so Storefront can render inline.
    Malformed bodies still surface as 4xx via spectree.
    """

    URL = "/api/v1/tenant-management/tenants/preview-adapter"

    def test_mock_adapter_returns_canned_metadata(self, client, auth_headers, real_adapter_test_disabled):
        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={"adapter": {"type": "mock", "dry_run": True}},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["network_name"] == "Mock Network"
        assert body["currency_code"] == "USD"
        assert body["time_zone"] == "UTC"
        assert body["inventory_reachable"] is True

    def test_gam_happy_path_returns_network_metadata(self, client, auth_headers, monkeypatch):
        """Stub ``preview_adapter`` to simulate a successful GAM probe."""
        from src.admin.services.adapter_connection_tester import AdapterPreview
        import src.admin.tenant_management_api as api_module

        monkeypatch.setattr(
            api_module,
            "preview_adapter",
            lambda atype, cfg: AdapterPreview(
                ok=True,
                network_name="Acme News",
                network_code="123456",
                currency_code="USD",
                time_zone="America/New_York",
                inventory_reachable=True,
            ),
        )

        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={
                "adapter": {
                    "type": "google_ad_manager",
                    "network_code": "123456",
                    "service_account_email": "sa@example.com",
                    "service_account_key_json": '{"type":"service_account"}',
                }
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["network_name"] == "Acme News"
        assert body["currency_code"] == "USD"
        assert body["time_zone"] == "America/New_York"

    def test_bad_creds_return_200_with_ok_false(self, client, auth_headers, monkeypatch):
        """Bad creds are a normal flow — surface as 200 + ok=false, not 4xx."""
        from src.admin.services.adapter_connection_tester import AdapterPreview
        import src.admin.tenant_management_api as api_module

        monkeypatch.setattr(
            api_module,
            "preview_adapter",
            lambda atype, cfg: AdapterPreview(ok=False, error="invalid_grant", inventory_reachable=False),
        )

        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={
                "adapter": {
                    "type": "google_ad_manager",
                    "network_code": "123456",
                    "service_account_email": "sa@example.com",
                    "service_account_key_json": '{"type":"bad"}',
                }
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["inventory_reachable"] is False
        assert body["error"] == "invalid_grant"

    def test_no_tenant_row_created(self, client, auth_headers, real_adapter_test_disabled):
        """Preview must not create any tenant row as a side effect."""
        from sqlalchemy import func

        with get_db_session() as session:
            count_before = session.scalar(select(func.count()).select_from(Tenant))

        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={"adapter": {"type": "mock"}},
        )
        assert resp.status_code == 200

        with get_db_session() as session:
            count_after = session.scalar(select(func.count()).select_from(Tenant))
        assert count_after == count_before

    def test_malformed_body_returns_422(self, client, auth_headers):
        """Pydantic validation failure surfaces as 422, not 200 + ok=false."""
        resp = client.post(self.URL, headers=auth_headers, json={"adapter": {"type": "unknown"}})
        assert resp.status_code == 422

    def test_missing_api_key_returns_401(self, client):
        resp = client.post(self.URL, json={"adapter": {"type": "mock"}})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Sprint 1.5: GET /tenants/{tid}/status
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_status_cache():
    """Bust the in-memory status cache between tests so state doesn't leak."""
    from src.admin.services.tenant_status_service import invalidate_status_cache

    invalidate_status_cache()
    yield
    invalidate_status_cache()


class TestTenantStatus:
    """``GET /tenants/{tid}/status`` — consolidated operational snapshot."""

    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_status")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    def test_returns_404_for_unknown_tenant(self, client, auth_headers):
        resp = client.get("/api/v1/tenant-management/tenants/tenant_no_such_id/status", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "tenant_not_found"

    def test_freshly_provisioned_tenant_returns_zero_state(self, client, auth_headers, managed_tenant):
        """A new tenant has no syncs / workflows / buys / creatives — should return zero counts, not error."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()

        # Adapter block populated from provision
        assert body["adapter"]["type"] == "google_ad_manager"
        assert body["adapter"]["connected"] is True

        # Empty defaults
        assert body["syncs"]["inventory"]["status"] == "never_run"
        assert body["syncs"]["custom_targeting"]["status"] == "never_run"
        assert body["syncs"]["advertisers"]["status"] == "never_run"
        assert body["workflows"]["open_count"] == 0
        assert body["workflows"]["by_kind"] == {}
        assert body["media_buys"]["active_count"] == 0
        assert body["media_buys"]["pending_approval_count"] == 0
        assert body["packages"]["active_count"] == 0
        assert body["packages"]["last_24h_impressions"] == 0
        assert body["creatives"]["active_count"] == 0
        assert body["creatives"]["pending_review_count"] == 0
        assert body["webhooks"] is None
        assert "fetched_at" in body

    def test_status_reflects_active_media_buy(self, client, auth_headers, managed_tenant, bound_factories):
        """An active media buy bumps ``media_buys.active_count``."""
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="p_status",
            name="Status Test",
            platform_mappings={"google_ad_manager": {"advertiser_id": "x"}},
            access_token="t_status",
        )
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_status_active",
            order_name="Status Active",
            advertiser_name="x",
            status="active",
            budget=100,
            start_date=datetime.now(UTC).date(),
            end_date=datetime.now(UTC).date(),
            raw_request={},
        )

        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["media_buys"]["active_count"] == 1

    def test_status_is_cached_within_ttl(self, client, auth_headers, managed_tenant):
        """Two calls within the TTL window return the same ``fetched_at`` (cache hit)."""
        first = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers).get_json()
        second = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers).get_json()
        assert first["fetched_at"] == second["fetched_at"]

    def test_adapter_test_invalidates_status_cache(self, client, auth_headers, managed_tenant):
        """Calling the adapter test-connection endpoint busts the status cache.

        Verifies the invalidation hook wired in ``adapter_test_connection``.
        """
        first = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers).get_json()
        # Touch the test-connection endpoint — should invalidate.
        client.post(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config/test-connection",
            headers=auth_headers,
        )
        second = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers).get_json()
        assert first["fetched_at"] != second["fetched_at"]

    def test_missing_api_key_returns_401(self, client, managed_tenant):
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Sprint 1.6: pre-map advertisers
# ---------------------------------------------------------------------------


class TestPreMapAdvertiser:
    """``POST /tenants/{tid}/accounts`` and ``GET .../accounts``.

    Verifies the Account upsert-by-natural-key behavior with a pre-attached
    ``platform_mappings.google_ad_manager.advertiser_id``.
    """

    @pytest.fixture
    def tid(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_premap")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        t = resp.get_json()["tenant_id"]
        cleanup_tenants.append(t)
        return t

    def _post_account(self, client, auth_headers, tid, **overrides):
        body = {
            "operator": "accuweather.com",
            "brand": {"domain": "cocacola.com"},
            "billing": "operator",
            "gam_advertiser_id": "12345",
            "gam_advertiser_name": "Coca-Cola (AccuWeather)",
        }
        body.update(overrides)
        return client.post(
            f"/api/v1/tenant-management/tenants/{tid}/accounts",
            headers=auth_headers,
            json=body,
        )

    def test_create_with_pre_attached_advertiser_returns_201(self, client, auth_headers, tid):
        resp = self._post_account(client, auth_headers, tid)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["status"] == "active"
        assert body["billing"] == "operator"
        assert body["gam_advertiser_id"] == "12345"
        assert body["gam_advertiser_name"] == "Coca-Cola (AccuWeather)"
        assert body["advertiser_mapped"] is True
        # Auto-generated name template
        assert "accuweather.com" in body["name"] and "cocacola.com" in body["name"]

    def test_repeat_post_upserts_existing_account(self, client, auth_headers, tid):
        first = self._post_account(client, auth_headers, tid)
        assert first.status_code == 201
        first_account_id = first.get_json()["account_id"]

        # Re-POST with the same natural key but different advertiser id
        second = self._post_account(client, auth_headers, tid, gam_advertiser_id="99999")
        assert second.status_code == 200
        body = second.get_json()
        assert body["account_id"] == first_account_id  # Same row
        assert body["gam_advertiser_id"] == "99999"  # Updated

    def test_billing_agent_requires_buyer_agent_principal_id(self, client, auth_headers, tid):
        resp = self._post_account(client, auth_headers, tid, billing="agent")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "buyer_agent_required"

    def test_billing_agent_separates_per_agent(self, client, auth_headers, tid):
        """Two buyer agents on the same (operator, brand) → two distinct Accounts."""
        a1 = self._post_account(
            client,
            auth_headers,
            tid,
            billing="agent",
            buyer_agent_principal_id="scope3-buyer",
            gam_advertiser_id="agent_adv_1",
        )
        a2 = self._post_account(
            client,
            auth_headers,
            tid,
            billing="agent",
            buyer_agent_principal_id="other-buyer",
            gam_advertiser_id="agent_adv_2",
        )
        assert a1.status_code == 201
        assert a2.status_code == 201
        assert a1.get_json()["account_id"] != a2.get_json()["account_id"]
        assert a1.get_json()["buyer_agent_principal_id"] == "scope3-buyer"
        assert a2.get_json()["buyer_agent_principal_id"] == "other-buyer"

    def test_sandbox_rejects_advertiser_id(self, client, auth_headers, tid):
        resp = self._post_account(
            client,
            auth_headers,
            tid,
            sandbox=True,
            brand={"domain": "test.example"},
            gam_advertiser_id="should_not_be_accepted",
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "sandbox_advertiser_managed"

    def test_sandbox_creates_unmapped_account(self, client, auth_headers, tid):
        resp = self._post_account(
            client,
            auth_headers,
            tid,
            sandbox=True,
            brand={"domain": "test.example"},
            gam_advertiser_id=None,
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["sandbox"] is True
        # Sandbox accounts are unmapped at creation time — sprint 1.6 impl
        # will route them to the per-tenant sandbox advertiser at first-buy.
        assert body["advertiser_mapped"] is False

    def test_post_unknown_tenant_returns_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/tenant-management/tenants/tenant_missing/accounts",
            headers=auth_headers,
            json={
                "operator": "x",
                "brand": {"domain": "y.com"},
                "billing": "operator",
                "gam_advertiser_id": "1",
            },
        )
        assert resp.status_code == 404

    def test_list_returns_pre_mapped_accounts(self, client, auth_headers, tid):
        self._post_account(client, auth_headers, tid, brand={"domain": "a.com"})
        self._post_account(client, auth_headers, tid, brand={"domain": "b.com"})

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/accounts", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 2
        domains = {a["brand"]["domain"] for a in body["accounts"]}
        assert domains == {"a.com", "b.com"}
        for a in body["accounts"]:
            assert a["advertiser_mapped"] is True

    def test_list_filters_by_advertiser_mapped(self, client, auth_headers, tid):
        self._post_account(client, auth_headers, tid, brand={"domain": "mapped.com"})
        self._post_account(
            client,
            auth_headers,
            tid,
            sandbox=True,
            brand={"domain": "sandbox.com"},
            gam_advertiser_id=None,
        )

        mapped = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/accounts?advertiser_mapped=true",
            headers=auth_headers,
        )
        unmapped = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/accounts?advertiser_mapped=false",
            headers=auth_headers,
        )

        assert mapped.get_json()["count"] == 1
        assert mapped.get_json()["accounts"][0]["brand"]["domain"] == "mapped.com"
        assert unmapped.get_json()["count"] == 1
        assert unmapped.get_json()["accounts"][0]["brand"]["domain"] == "sandbox.com"

    def test_list_filters_by_operator(self, client, auth_headers, tid):
        self._post_account(client, auth_headers, tid, operator="op-a", brand={"domain": "x.com"})
        self._post_account(client, auth_headers, tid, operator="op-b", brand={"domain": "x.com"})

        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/accounts?operator=op-a",
            headers=auth_headers,
        )
        body = resp.get_json()
        assert body["count"] == 1
        assert body["accounts"][0]["operator"] == "op-a"

    def test_missing_api_key_returns_401(self, client, tid):
        resp = client.post(
            f"/api/v1/tenant-management/tenants/{tid}/accounts",
            json={"operator": "x", "brand": {"domain": "y.com"}, "billing": "operator", "gam_advertiser_id": "1"},
        )
        assert resp.status_code in (401, 403)
