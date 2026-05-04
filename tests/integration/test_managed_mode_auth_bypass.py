"""Integration tests for sprint 2 managed-mode auth bypass.

When ``MANAGED_INSTANCE=true`` and a tenant is ``managed_externally``,
``X-Identity-*`` headers from the upstream proxy authorize the request
without going through the salesagent's Google OAuth flow.

Failure modes match docs/integration/managed-mode-identity-contract.md:

- Managed tenant + missing headers → 403 ``identity_required``
- Managed tenant + ``X-Identity-Org-Id`` doesn't match
  ``tenant.external_org_id`` → 403 ``identity_org_mismatch``
- Managed tenant + valid headers → request passes auth (200/302/etc.,
  whatever the route returns)
- Open-instance tenant on a managed instance → falls through to OAuth
  redirect (today's behavior preserved)
- ``MANAGED_INSTANCE`` unset → bypass disabled, OAuth required for all
  tenants regardless of ``managed_externally`` flag
"""

from __future__ import annotations

import uuid

import pytest
from flask import Flask

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from tests.helpers.managed_tenant_api import install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-managed-mode-auth-test-key"


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture
def app(integration_db, install_api_key):
    """Build an app that includes both the management API + the per-tenant
    admin routes (tenants_bp). The bypass lives on tenants_bp's dashboard
    handler via require_tenant_access."""
    from src.admin.app import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def managed_tenant(integration_db):
    """Insert a managed tenant directly — bypasses the management API to
    keep the fixture cheap and self-contained."""
    from sqlalchemy import select

    from src.core.database.models import (
        AdapterConfig,
        CurrencyLimit,
        Principal,
        PropertyTag,
    )

    tid = f"t_man_{uuid.uuid4().hex[:8]}"
    org_id = f"org_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        # The model-layer write guard requires ``management_api_caller`` to
        # insert managed_externally=True. Tests bypass the actual API for
        # speed; this flag is the same one the API endpoint sets.
        session.info["management_api_caller"] = True
        session.add(
            Tenant(
                tenant_id=tid,
                name="Managed Auth Test",
                subdomain=tid,
                ad_server="mock",
                is_active=True,
                billing_plan="standard",
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_format_ids=[],
                policy_settings={},
                managed_externally=True,
                external_org_id=org_id,
                external_source="scope3",
            )
        )
        session.commit()
    yield {"tenant_id": tid, "external_org_id": org_id}
    with get_db_session() as session:
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _identity_headers(org_id: str, *, role: str = "admin") -> dict[str, str]:
    return {
        "X-Identity-Email": "user@scope3.example",
        "X-Identity-Org-Id": org_id,
        "X-Identity-Role": role,
        "X-Identity-Source": "scope3",
        "X-Identity-User-Id": "user-123",
    }


# ---------------------------------------------------------------------------
# MANAGED_INSTANCE=true + managed_externally=True
# ---------------------------------------------------------------------------


class TestManagedModeAuthBypass:
    def test_valid_headers_authorize_dashboard(self, client, managed_tenant, monkeypatch):
        """Valid X-Identity-* + matching org_id → dashboard renders (200)."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        # 200 OK (dashboard rendered) or 302 (further internal redirect),
        # but NOT 302 to /login — that would mean auth failed.
        assert resp.status_code in (200, 302), resp.get_data(as_text=True)
        if resp.status_code == 302:
            assert "login" not in (resp.location or ""), (
                f"unexpected redirect to login: {resp.location}"
            )

    def test_missing_headers_returns_403_identity_required(
        self, client, managed_tenant, monkeypatch
    ):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(f"/tenant/{managed_tenant['tenant_id']}")
        assert resp.status_code == 403
        body = resp.get_data(as_text=True)
        assert "identity_required" in body, body

    def test_org_id_mismatch_returns_403(self, client, managed_tenant, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers("wrong_org_id"),
        )
        assert resp.status_code == 403
        body = resp.get_data(as_text=True)
        assert "identity_org_mismatch" in body, body

    def test_invalid_role_returns_403_identity_required(
        self, client, managed_tenant, monkeypatch
    ):
        """X-Identity-Role outside admin|member|viewer enum → 403.

        The reader raises InvalidPropagatedIdentity which the bypass
        translates to identity_required (header set is malformed).
        """
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers(managed_tenant["external_org_id"], role="superuser"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Bypass is opt-in — environment toggles
# ---------------------------------------------------------------------------


class TestBypassIsOptIn:
    def test_managed_instance_unset_falls_through_to_oauth(
        self, client, managed_tenant, monkeypatch
    ):
        """Without MANAGED_INSTANCE=true, X-Identity-* headers are ignored
        and the request hits the normal OAuth gate (302 to /login)."""
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        # Disable test mode too so we don't accidentally pass via test_user
        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "false")
        resp = client.get(
            f"/tenant/{managed_tenant['tenant_id']}",
            headers=_identity_headers(managed_tenant["external_org_id"]),
        )
        assert resp.status_code == 302
        assert "login" in (resp.location or "")

    def test_open_instance_tenant_on_managed_deployment_uses_oauth(
        self, client, integration_db, monkeypatch
    ):
        """Tenant with managed_externally=False on a MANAGED_INSTANCE=true
        deployment falls through to OAuth — managed instances still host
        legacy/staff open-instance tenants."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "false")

        tid = f"t_open_{uuid.uuid4().hex[:8]}"
        with get_db_session() as session:
            session.add(
                Tenant(
                    tenant_id=tid,
                    name="Open Instance Tenant",
                    subdomain=tid,
                    ad_server="mock",
                    is_active=True,
                    billing_plan="standard",
                    authorized_emails=[],
                    authorized_domains=[],
                    auto_approve_format_ids=[],
                    policy_settings={},
                    managed_externally=False,
                )
            )
            session.commit()

        try:
            resp = client.get(
                f"/tenant/{tid}",
                headers=_identity_headers("any_org_id"),
            )
            assert resp.status_code == 302
            assert "login" in (resp.location or "")
        finally:
            with get_db_session() as session:
                session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
                session.commit()
