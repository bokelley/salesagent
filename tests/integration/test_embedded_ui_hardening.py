"""Sprint 4 — UI hardening for embedded-mode tenants.

The publisher-facing admin UI hides platform-managed surfaces when
``tenant.is_embedded=True``. See
``docs/design/embedded-mode-sprint-4-ui-hardening.md``.

Covered surfaces:
- ``/tenant/{id}/users`` returns 200 with the lock banner instead of the
  user list (so deep-links from the setup-task panel land on an
  explanation rather than a dead end).
- ``/tenant/{id}/settings`` omits Account → Org Info / Branding / Domain
  Configuration / Access Control sections, the entire Ad Server
  Configuration section, and renders currency read-only (no add/remove
  UI).
- Open-instance tenants on the same deployment continue to render the
  full editable UI — the hide is keyed off ``tenant.is_embedded`` only.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Test app + auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(integration_db, monkeypatch):
    """Build the full admin Flask app — exercises real route registrations
    and the real Jinja templates."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    # Make sure embedded-mode header bypass doesn't intervene — these tests
    # exercise the read-path UI surface, not the X-Identity-* contract.
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)

    from src.admin.app import create_app

    application = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    # Test super-admin session bypasses the OAuth check — see
    # ``require_tenant_access`` (test_user + super_admin role).
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": "admin@example.com", "name": "Admin"}
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = "*"
    return c


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------


def _insert_tenant(*, is_embedded: bool, external_source: str | None = None) -> str:
    """Insert a minimal Tenant + a USD CurrencyLimit so the Settings page
    has currency rows to render. Bypasses the model-layer write guard the
    same way ``test_managed_mode_auth_bypass.py`` does."""

    tid = f"t_{'man' if is_embedded else 'open'}_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = Tenant(
            tenant_id=tid,
            name="UI Hardening Test",
            subdomain=tid,
            ad_server="mock",
            is_active=True,
            billing_plan="standard",
            authorized_emails=[],
            authorized_domains=[],
            auto_approve_format_ids=[],
            policy_settings={},
            is_embedded=is_embedded,
            external_source=external_source,
            external_org_id=f"org_{uuid.uuid4().hex[:8]}" if is_embedded else None,
        )
        session.add(tenant)
        session.add(CurrencyLimit(tenant_id=tid, currency_code="USD"))
        session.commit()
    return tid


@pytest.fixture
def embedded_tenant_id(integration_db):
    tid = _insert_tenant(is_embedded=True, external_source="scope3")
    yield tid
    _cleanup(tid)


@pytest.fixture
def open_tenant_id(integration_db):
    tid = _insert_tenant(is_embedded=False)
    yield tid
    _cleanup(tid)


def _cleanup(tid: str) -> None:
    from src.core.database.models import AdapterConfig, Principal, PropertyTag

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


# ---------------------------------------------------------------------------
# /tenant/{id}/users
# ---------------------------------------------------------------------------


class TestUsersRouteHiddenOnEmbedded:
    def test_embedded_tenant_returns_200_with_banner(self, client, embedded_tenant_id):
        """Embedded tenant: 200 + lock banner — NOT 404. Deep-links from
        the setup-tasks panel must land on an explanation."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/users")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Platform settings managed by" in body
        assert "Scope3" in body  # external_source was "scope3" → titled "Scope3"

    def test_embedded_tenant_omits_user_management_form(self, client, embedded_tenant_id):
        """The Add User form must NOT render on embedded tenants."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/users")
        body = resp.get_data(as_text=True)
        # The Add User form posts to /add — confirm the form action is gone.
        assert "/users/add" not in body
        # Domain/email management forms also belong on this page.
        assert 'name="email"' not in body or "Add User" not in body

    def test_open_tenant_renders_user_list(self, client, open_tenant_id):
        """Open-instance tenants are unaffected by the hide."""
        resp = client.get(f"/tenant/{open_tenant_id}/users")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Platform settings managed by" not in body
        # The Add User form action is present on the open-instance page.
        assert "/users/add" in body


# ---------------------------------------------------------------------------
# /tenant/{id}/settings
# ---------------------------------------------------------------------------


class TestSettingsHiddenSectionsOnEmbedded:
    def test_embedded_omits_organization_information(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        # The Org-Info form posts to /settings/general. On embedded tenants
        # this form (and its tenant-name input) must be hidden.
        assert "/settings/general" not in body
        assert 'name="name"' not in body
        # Banner is present on the locked Account section.
        assert "Platform settings managed by" in body

    def test_embedded_omits_branding_section(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Branding heading and favicon-upload form action.
        assert "<h3>Branding</h3>" not in body
        assert "/upload_favicon" not in body

    def test_embedded_omits_access_control_section(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Access Control heading + the domain/email POST endpoints.
        assert "Access Control" not in body
        assert "/domains/add" not in body
        assert "/emails/add" not in body

    def test_embedded_omits_ad_server_configuration(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # The Ad Server section has a unique heading and adapter-card markup.
        assert "Ad Server Configuration" not in body
        assert "Available Ad Servers" not in body
        assert "selectGAMAdapter()" not in body

    def test_embedded_omits_settings_nav_tabs(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # Account + Users & Access + Ad Server tabs are hidden in the rail.
        # Look for the data-section attributes that drive the tab links.
        assert 'data-section="account"' not in body
        assert 'data-section="adserver"' not in body
        # Users-tab also drops on embedded.
        assert "Users &amp; Access" not in body and "Users & Access" not in body

    def test_open_tenant_renders_all_sections(self, client, open_tenant_id):
        """Regression guard: every hidden surface still renders for
        open-instance tenants."""
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "/settings/general" in body
        assert "<h3>Branding</h3>" in body
        assert "Access Control" in body
        assert "Ad Server Configuration" in body
        assert 'data-section="account"' in body
        assert 'data-section="adserver"' in body


# ---------------------------------------------------------------------------
# Currency lock
# ---------------------------------------------------------------------------


class TestCurrencyLockOnEmbedded:
    def test_embedded_currency_renders_read_only(self, client, embedded_tenant_id):
        """No add/remove inputs — currency is provisioned by the upstream
        platform and surfaces as plain text."""
        resp = client.get(f"/tenant/{embedded_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        # The currency value itself must be visible.
        assert "USD" in body
        # The editable inputs and add-modal trigger must NOT be present.
        assert "showAddCurrencyModal()" not in body
        assert 'name="currency_limits[USD][min_package_budget]"' not in body
        assert 'name="currency_limits[USD][max_daily_package_spend]"' not in body
        # And the modal markup itself (with id="new-currency-code") is gone.
        assert 'id="new-currency-code"' not in body

    def test_open_tenant_renders_editable_currency_ui(self, client, open_tenant_id):
        resp = client.get(f"/tenant/{open_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert "showAddCurrencyModal()" in body
        assert 'name="currency_limits[USD][min_package_budget]"' in body
        assert 'id="new-currency-code"' in body


# ---------------------------------------------------------------------------
# Banner partial sanity
# ---------------------------------------------------------------------------


class TestLockBanner:
    def test_banner_uses_external_source_title_case(self, client, integration_db):
        """A tenant flagged ``external_source='scope3'`` should surface as
        "Scope3" in the banner — the partial titlecases the value."""
        tid = _insert_tenant(is_embedded=True, external_source="scope3")
        try:
            resp = client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            assert "Platform settings managed by Scope3" in body
        finally:
            _cleanup(tid)

    def test_banner_falls_back_when_external_source_is_null(self, client, integration_db):
        """``external_source`` may be NULL on edge-case embedded tenants;
        the partial defaults to ``"your platform"`` (then titlecases)."""
        tid = _insert_tenant(is_embedded=True, external_source=None)
        try:
            resp = client.get(f"/tenant/{tid}/users")
            body = resp.get_data(as_text=True)
            # default('your platform') | title → "Your Platform"
            assert "Platform settings managed by Your Platform" in body
        finally:
            _cleanup(tid)
