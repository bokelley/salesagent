"""Integration tests for the embed-mode breadcrumb root override.

Two surfaces:

- Schema/storage: ``embed_breadcrumb_root`` round-trips through
  provision, GET, and PATCH on the Tenant Management API; bad input
  is rejected by the Pydantic validator (422).
- Rendering: the shared ``_breadcrumb.html`` partial renders the
  configured override on embedded tenants, ignores it on open-instance
  tenants, and prefers the ``X-Embed-Breadcrumb-Root`` header over the
  persisted column when both are set.
"""

from __future__ import annotations

import json
import uuid

import pytest
from flask import Flask

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    Principal,
    PropertyTag,
    Tenant,
)
from tests.helpers.managed_tenant_api import install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-embed-breadcrumb-test-key"


# ---------------------------------------------------------------------------
# Tenant Management API fixtures (mirror test_managed_tenant_api.py)
# ---------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def _stub_adapter_test(monkeypatch):
    """Default adapter probe to success — these tests exercise schema/storage,
    not real adapter wiring."""
    import src.admin.tenant_management_api as api_module

    monkeypatch.setattr(api_module, "test_adapter_connection", lambda *_args, **_kwargs: (True, None))


@pytest.fixture
def cleanup_tenants():
    created: list[str] = []
    yield created
    if not created:
        return
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for tid in created:
            for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
                session.execute(model.__table__.delete().where(model.tenant_id == tid))
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _provision_payload(**overrides):
    payload = {
        "name": "Acme News",
        "external_org_id": "org_acme_breadcrumb",
        "external_source": "scope3",
        "contact_email": "ops@example.com",
        "public_agent_url": "https://interchange.io",
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
# Storage: provision + patch + GET round-trip
# ---------------------------------------------------------------------------


class TestEmbedBreadcrumbRootStorage:
    def test_provision_persists_breadcrumb_root(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(
            external_org_id="org_breadcrumb_provision",
            embed_breadcrumb_root={"label": "Customer 1000", "url": "https://host.example/customers/1000"},
        )
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        detail = client.get(f"/api/v1/tenant-management/tenants/{tid}", headers=auth_headers)
        assert detail.status_code == 200
        body = detail.get_json()
        assert body["embed_breadcrumb_root"] == {
            "label": "Customer 1000",
            "url": "https://host.example/customers/1000",
        }

    def test_patch_updates_breadcrumb_root(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_patch")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront", "url": "https://host.example/store"}},
        )
        assert patch.status_code == 200, patch.get_data(as_text=True)
        body = patch.get_json()
        assert body["embed_breadcrumb_root"] == {
            "label": "Storefront",
            "url": "https://host.example/store",
        }

    def test_patch_omitted_field_preserves_value(self, client, auth_headers, cleanup_tenants):
        """Spec: PATCH with the field absent leaves the existing override
        unchanged (matches public_agent_url semantics)."""
        payload = _provision_payload(
            external_org_id="org_breadcrumb_omit",
            embed_breadcrumb_root={"label": "Original", "url": "https://host.example/orig"},
        )
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # PATCH some other field — embed_breadcrumb_root must persist.
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"name": "Renamed"},
        )
        assert patch.status_code == 200, patch.get_data(as_text=True)
        body = patch.get_json()
        assert body["embed_breadcrumb_root"] == {
            "label": "Original",
            "url": "https://host.example/orig",
        }

    def test_patch_rejects_empty_label(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_empty_label")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "", "url": "https://host.example/x"}},
        )
        assert patch.status_code == 422

    def test_patch_rejects_oversized_label(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_long_label")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "x" * 121, "url": "https://host.example/x"}},
        )
        assert patch.status_code == 422

    def test_patch_rejects_non_https_url(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_non_https")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront", "url": "http://host.example/store"}},
        )
        assert patch.status_code == 422

    def test_patch_accepts_localhost_http_for_dev(self, client, auth_headers, cleanup_tenants):
        """``http://localhost`` and ``http://127.0.0.1`` are accepted so dev
        / Storefront local stacks can wire embed_breadcrumb_root without a
        TLS cert. Loopback only — broader http:// is still rejected."""
        payload = _provision_payload(external_org_id="org_breadcrumb_localhost")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # http://localhost — accepted
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront (dev)", "url": "http://localhost:3000/store"}},
        )
        assert patch.status_code == 200, patch.get_data(as_text=True)

        # http://127.0.0.1 — also accepted
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "Storefront (dev)", "url": "http://127.0.0.1:3000/store"}},
        )
        assert patch.status_code == 200

        # http://otherhost — still rejected (loopback exception only)
        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"embed_breadcrumb_root": {"label": "X", "url": "http://otherhost.example/store"}},
        )
        assert patch.status_code == 422

    def test_patch_rejects_extra_keys(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_breadcrumb_extra")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={
                "embed_breadcrumb_root": {
                    "label": "Storefront",
                    "url": "https://host.example/store",
                    "color": "blue",
                }
            },
        )
        assert patch.status_code == 422


# ---------------------------------------------------------------------------
# Rendering: embed-mode breadcrumb partial integration with admin app
# ---------------------------------------------------------------------------


def _insert_render_tenant(*, is_embedded: bool, embed_breadcrumb_root: dict | None) -> str:
    """Insert a minimal Tenant + USD CurrencyLimit so the /users page renders.
    Bypasses the model-layer write guard via management_api_caller marker."""
    tid = f"t_render_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = Tenant(
            tenant_id=tid,
            name="Render Test",
            subdomain=tid,
            ad_server="mock",
            is_active=True,
            billing_plan="standard",
            authorized_emails=[],
            authorized_domains=[],
            auto_approve_format_ids=[],
            policy_settings={},
            is_embedded=is_embedded,
            external_source="scope3" if is_embedded else None,
            external_org_id=f"org_{uuid.uuid4().hex[:8]}" if is_embedded else None,
            embed_breadcrumb_root=embed_breadcrumb_root,
        )
        session.add(tenant)
        session.add(CurrencyLimit(tenant_id=tid, currency_code="USD"))
        session.commit()
    return tid


def _cleanup_render_tenant(tid: str) -> None:
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


@pytest.fixture
def admin_app(integration_db, monkeypatch):
    """Build the full admin app for rendering tests."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)

    from src.admin.app import create_app

    application = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    return application


@pytest.fixture
def admin_client(admin_app):
    c = admin_app.test_client()
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": "admin@example.com", "name": "Admin"}
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = "*"
    return c


class TestEmbedBreadcrumbRendering:
    def test_embedded_tenant_renders_configured_root(self, admin_client):
        """Embedded tenant with a column override: rendered breadcrumb's
        first crumb is the configured label + URL, not the dashboard."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Customer 1000", "url": "https://host.example/customers/1000"},
        )
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert 'href="https://host.example/customers/1000"' in body
            assert "Customer 1000" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_open_instance_tenant_ignores_column(self, admin_client):
        """Open-instance tenants ignore the override even if set — host
        chrome only applies inside an embedded deployment."""
        tid = _insert_render_tenant(
            is_embedded=False,
            embed_breadcrumb_root={"label": "Customer 1000", "url": "https://host.example/customers/1000"},
        )
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Customer 1000" not in body
            # Default first crumb is the tenant name (rendered as the
            # dashboard link target).
            assert "Render Test" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_no_override_renders_default_first_crumb(self, admin_client):
        """Embedded tenant with no override + no header: default first
        crumb is the tenant name (linking to the salesagent dashboard)."""
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            # No host override surface; tenant name still present as first crumb.
            assert "Render Test" in body
            assert "host.example" not in body
        finally:
            _cleanup_render_tenant(tid)

    def test_header_overrides_column(self, admin_client):
        """Header takes precedence over the persisted column."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Column Value", "url": "https://host.example/column"},
        )
        try:
            resp = admin_client.get(
                f"/tenant/{tid}/users",
                headers={
                    "X-Embed-Breadcrumb-Root": json.dumps(
                        {"label": "Header Value", "url": "https://host.example/header"}
                    )
                },
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Header Value" in body
            assert 'href="https://host.example/header"' in body
            # Header wins — column value not rendered.
            assert "Column Value" not in body
        finally:
            _cleanup_render_tenant(tid)

    def test_invalid_header_json_falls_through_to_column(self, admin_client):
        """Malformed JSON header: log + ignore, render column value."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Column Value", "url": "https://host.example/column"},
        )
        try:
            resp = admin_client.get(
                f"/tenant/{tid}/users",
                headers={"X-Embed-Breadcrumb-Root": "not-json{"},
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Column Value" in body
            assert 'href="https://host.example/column"' in body
        finally:
            _cleanup_render_tenant(tid)

    def test_invalid_header_shape_falls_through_to_column(self, admin_client):
        """Header missing 'label': falls through to column value."""
        tid = _insert_render_tenant(
            is_embedded=True,
            embed_breadcrumb_root={"label": "Column Value", "url": "https://host.example/column"},
        )
        try:
            resp = admin_client.get(
                f"/tenant/{tid}/users",
                headers={"X-Embed-Breadcrumb-Root": json.dumps({"url": "https://host.example/header"})},
            )
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Column Value" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_last_crumb_has_no_link(self, admin_client):
        """The leaf crumb (current page) renders as a span, not an <a>."""
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            # Jinja HTML-escapes ``&`` to ``&amp;``; the leaf crumb
            # ("Users & Access") renders as a styled span, not a link.
            # The breadcrumb partial marks the leaf with aria-current="page".
            leaf = '<span class="breadcrumb-current" aria-current="page">Users &amp; Access</span>'
            assert leaf in body
            # The crumb container ends with the leaf — no <a> tag immediately
            # before it.
            preceding = body.split(leaf)[0][-200:]
            assert "<a " not in preceding[-50:]
        finally:
            _cleanup_render_tenant(tid)


# ---------------------------------------------------------------------------
# Sprint 4 follow-up: breadcrumb partial coverage across tenant pages.
# Each page must render the partial in both standalone and embedded modes,
# preserving an unbroken host-to-leaf trail in the iframe.
# ---------------------------------------------------------------------------


HOST_ROOT = {"label": "Storefront", "url": "https://host.example/store"}


def _assert_first_crumb_is_host(body: str) -> None:
    """The override label + URL appear inside a breadcrumb container."""
    assert 'class="breadcrumb"' in body
    assert "Storefront" in body
    assert 'href="https://host.example/store"' in body


def _assert_first_crumb_is_tenant(body: str) -> None:
    """Standalone mode: tenant name appears as the first crumb."""
    assert 'class="breadcrumb"' in body
    assert "Render Test" in body
    assert "host.example" not in body


class TestBreadcrumbsAcrossTenantPages:
    """Each major tenant page renders the embed-aware breadcrumb partial.

    Pages without DB scaffolding (products list, media buys list, etc.)
    only need a tenant + USD currency limit — render paths that need
    extra rows fail open with empty lists.
    """

    def test_dashboard_embedded_prepends_host(self, admin_client):
        """Dashboard has a single tenant-name crumb. In embed mode the
        host root is *prepended* (not replaced) so the iframe still
        shows where the user is inside the tenant."""
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            _assert_first_crumb_is_host(body)
            # Tenant name is still the leaf — prepending preserved it.
            assert "Render Test" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_dashboard_standalone_shows_tenant_only(self, admin_client):
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            _assert_first_crumb_is_tenant(body)
        finally:
            _cleanup_render_tenant(tid)

    def test_products_list_embedded_shows_host(self, admin_client):
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/products/")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            _assert_first_crumb_is_host(body)
            assert "Products" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_products_list_standalone_shows_tenant(self, admin_client):
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/products/")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            _assert_first_crumb_is_tenant(body)
        finally:
            _cleanup_render_tenant(tid)

    def test_media_buys_list_embedded_shows_host(self, admin_client):
        tid = _insert_render_tenant(is_embedded=True, embed_breadcrumb_root=HOST_ROOT)
        try:
            resp = admin_client.get(f"/tenant/{tid}/media-buys")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            _assert_first_crumb_is_host(body)
            assert "Media Buys" in body
        finally:
            _cleanup_render_tenant(tid)

    def test_media_buys_list_standalone_shows_tenant(self, admin_client):
        tid = _insert_render_tenant(is_embedded=False, embed_breadcrumb_root=None)
        try:
            resp = admin_client.get(f"/tenant/{tid}/media-buys")
            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            _assert_first_crumb_is_tenant(body)
        finally:
            _cleanup_render_tenant(tid)


class TestEmbedRootFilterUnit:
    """Direct unit coverage for the 1-crumb prepend branch.

    The dashboard page emits a single crumb (the tenant name as the
    current page). The filter must prepend the host root rather than
    replace it — otherwise the iframe loses the only navigation cue
    back to the upstream chrome.
    """

    def test_filter_prepends_when_one_crumb(self):
        from src.admin.utils.breadcrumbs import with_embed_root_filter

        crumbs = [{"label": "Acme News"}]
        result = with_embed_root_filter(crumbs, HOST_ROOT)
        assert result == [
            {"label": "Storefront", "url": "https://host.example/store"},
            {"label": "Acme News"},
        ]

    def test_filter_replaces_first_crumb_when_two(self):
        from src.admin.utils.breadcrumbs import with_embed_root_filter

        crumbs = [
            {"label": "Acme News", "url": "/tenant/acme/"},
            {"label": "Products"},
        ]
        result = with_embed_root_filter(crumbs, HOST_ROOT)
        assert result == [
            {"label": "Storefront", "url": "https://host.example/store"},
            {"label": "Products"},
        ]

    def test_filter_passthrough_when_no_root(self):
        from src.admin.utils.breadcrumbs import with_embed_root_filter

        crumbs = [{"label": "Acme News"}, {"label": "Products"}]
        result = with_embed_root_filter(crumbs, None)
        assert result == crumbs
