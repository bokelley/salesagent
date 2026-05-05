"""Sprint 5 workstream B — buyer-routing page scaffolding.

Read-only tests that verify ``GET /tenant/<id>/buyer-routing`` renders
the three-section layout against existing data.

See ``docs/design/embedded-mode-sprint-5-buyer-routing-ux.md``.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    Account,
    AdvertiserRoutingRule,
    CurrencyLimit,
    Tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# App + auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(integration_db, monkeypatch):
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)

    from src.admin.app import create_app

    return create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": "admin@example.com", "name": "Admin"}
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = "*"
    return c


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------


def _insert_tenant(*, is_embedded: bool, default_gam_advertiser_id: str | None = None) -> str:
    tid = f"t_br_{'emb' if is_embedded else 'open'}_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = Tenant(
            tenant_id=tid,
            name=f"Buyer Routing Test {tid[-4:]}",
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
            default_gam_advertiser_id=default_gam_advertiser_id,
        )
        session.add(tenant)
        session.add(CurrencyLimit(tenant_id=tid, currency_code="USD"))
        session.commit()
    return tid


def _cleanup(tid: str) -> None:
    from src.core.database.models import Principal, PropertyTag

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for model in (
            AdvertiserRoutingRule,
            Account,
            CurrencyLimit,
            PropertyTag,
            Principal,
        ):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


@pytest.fixture
def embedded_tenant_id(integration_db):
    tid = _insert_tenant(is_embedded=True, default_gam_advertiser_id="adv_default_emb")
    yield tid
    _cleanup(tid)


@pytest.fixture
def standalone_tenant_id(integration_db):
    tid = _insert_tenant(is_embedded=False, default_gam_advertiser_id="adv_default_open")
    yield tid
    _cleanup(tid)


@pytest.fixture
def unactivated_tenant_id(integration_db):
    """Tenant with no default_gam_advertiser_id — should show the
    ``Tenant not activated`` banner."""
    tid = _insert_tenant(is_embedded=False, default_gam_advertiser_id=None)
    yield tid
    _cleanup(tid)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_routing_rule(tenant_id: str, **fields) -> str:
    rule_id = f"rule_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        session.add(
            AdvertiserRoutingRule(
                id=rule_id,
                tenant_id=tenant_id,
                operator_domain=fields.get("operator_domain", "wpp.com"),
                brand_house=fields.get("brand_house"),
                brand_id=fields.get("brand_id"),
                gam_advertiser_id=fields.get("gam_advertiser_id", "adv_111"),
            )
        )
        session.commit()
    return rule_id


def _add_account(tenant_id: str, *, account_id: str | None = None, **fields) -> str:
    aid = account_id or f"acct_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        session.add(
            Account(
                tenant_id=tenant_id,
                account_id=aid,
                name=fields.get("name", aid),
                status=fields.get("status", "active"),
                operator=fields.get("operator", "wpp.com"),
                brand=fields.get("brand", {"domain": "coca-cola.com"}),
                billing=fields.get("billing", "agent"),
                sandbox=fields.get("sandbox", False),
                principal_id=fields.get("principal_id"),
                platform_mappings=fields.get(
                    "platform_mappings", {"google_ad_manager": {"advertiser_id": "adv_99"}}
                ),
                resolved_via=fields.get("resolved_via", "exact"),
            )
        )
        session.commit()
    return aid


# ---------------------------------------------------------------------------
# Page renders for both tenant types
# ---------------------------------------------------------------------------


class TestBuyerRoutingPageRenders:
    def test_embedded_tenant_returns_200(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/buyer-routing")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_standalone_tenant_returns_200(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_unknown_tenant_returns_404(self, client, integration_db):
        resp = client.get("/tenant/no_such_tenant/buyer-routing")
        assert resp.status_code == 404

    def test_breadcrumb_present(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert 'class="breadcrumb"' in body
        assert "Buyer Routing" in body

    def test_three_section_headers_present(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert 'data-section="default-advertiser"' in body
        assert 'data-section="routing-rules"' in body
        assert 'data-section="recent-activity"' in body
        assert 'data-section="sandbox"' in body
        assert "Default GAM advertiser" in body
        assert "Routing rules" in body
        assert "Recent activity" in body
        assert "Sandbox accounts" in body


# ---------------------------------------------------------------------------
# Default advertiser display
# ---------------------------------------------------------------------------


class TestDefaultAdvertiserDisplay:
    def test_unactivated_tenant_shows_banner(self, client, unactivated_tenant_id):
        resp = client.get(f"/tenant/{unactivated_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert 'data-testid="not-activated-banner"' in body
        assert "Tenant not activated" in body
        assert "TENANT_NOT_ACTIVATED" in body

    def test_activated_tenant_shows_advertiser_id(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "adv_default_open" in body
        # Banner must NOT show when default is set.
        assert 'data-testid="not-activated-banner"' not in body


# ---------------------------------------------------------------------------
# Routing rules table
# ---------------------------------------------------------------------------


class TestRoutingRulesTable:
    def test_empty_state(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "No routing rules yet" in body

    def test_rule_renders_with_em_dash_for_nulls(self, client, standalone_tenant_id):
        _add_routing_rule(
            standalone_tenant_id,
            operator_domain="wpp.com",
            brand_house=None,
            brand_id=None,
            gam_advertiser_id="adv_wpp",
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "wpp.com" in body
        assert "adv_wpp" in body
        # Em-dash present for the NULL brand_house / brand_id columns.
        assert "—" in body

    def test_add_rule_button_disabled(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "+ Add rule" in body
        assert "Editor coming in workstream C" in body
        # Disabled — never POSTs to anything yet.
        assert 'class="br-disabled-btn" disabled' in body

    def test_embedded_tenant_hides_agent_column(self, client, embedded_tenant_id):
        _add_routing_rule(
            embedded_tenant_id,
            operator_domain="wpp.com",
            gam_advertiser_id="adv_emb",
        )
        resp = client.get(f"/tenant/{embedded_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # The Agent column header must NOT appear in embedded mode.
        # We assert the column header text doesn't appear inside any <th>.
        assert "<th>Agent</th>" not in body

    def test_standalone_tenant_shows_agent_column(self, client, standalone_tenant_id):
        _add_routing_rule(
            standalone_tenant_id,
            operator_domain="wpp.com",
            gam_advertiser_id="adv_sa",
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "<th>Agent</th>" in body


# ---------------------------------------------------------------------------
# Recent activity table
# ---------------------------------------------------------------------------


class TestRecentActivityTable:
    def test_empty_state(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "No buyer activity in the last 30 days" in body

    @pytest.mark.parametrize(
        "resolved_via,badge_class",
        [
            ("exact", "bg-success"),
            ("house", "bg-primary"),
            ("operator", "bg-info"),
            ("default", "bg-warning"),
            ("account", "bg-purple"),
            # "unknown" is the API surface for legacy NULL rows — covered
            # by ``test_legacy_null_resolved_via_renders_unknown`` since
            # the DB ``ck_accounts_resolved_via`` constraint won't store it.
        ],
    )
    def test_activity_row_renders_correct_badge(
        self,
        client,
        standalone_tenant_id,
        resolved_via,
        badge_class,
    ):
        _add_account(
            standalone_tenant_id,
            operator=f"op-{resolved_via}.example",
            resolved_via=resolved_via,
            sandbox=False,
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # Each resolved_via lands in a Bootstrap-style badge with the
        # right color class — that's the contract.
        assert f'<span class="badge {badge_class}">{resolved_via}</span>' in body

    def test_legacy_null_resolved_via_renders_unknown(self, client, standalone_tenant_id):
        _add_account(
            standalone_tenant_id,
            operator="legacy.example",
            resolved_via=None,
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert '<span class="badge bg-secondary">unknown</span>' in body

    def test_promote_button_disabled(self, client, standalone_tenant_id):
        _add_account(standalone_tenant_id, operator="promote.example")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "Promote" in body
        assert "Promotion coming in workstream E" in body

    def test_embedded_tenant_hides_agent_column_in_activity(self, client, embedded_tenant_id):
        _add_account(embedded_tenant_id, operator="emb.example")
        resp = client.get(f"/tenant/{embedded_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # No Agent column header anywhere in the rendered HTML.
        assert "<th>Agent</th>" not in body


# ---------------------------------------------------------------------------
# Sandbox section
# ---------------------------------------------------------------------------


class TestSandboxSection:
    def test_sandbox_collapsed_by_default(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # <details> renders collapsed by default — no `open` attribute.
        assert "<details>" in body
        assert "<details open>" not in body

    def test_sandbox_count_reflects_sandbox_accounts(self, client, standalone_tenant_id):
        _add_account(standalone_tenant_id, operator="prod.example", sandbox=False)
        _add_account(
            standalone_tenant_id,
            account_id="acct_sandbox_a",
            operator="sandbox-a.example",
            sandbox=True,
            resolved_via="sandbox",
        )
        _add_account(
            standalone_tenant_id,
            account_id="acct_sandbox_b",
            operator="sandbox-b.example",
            sandbox=True,
            resolved_via="sandbox",
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "Sandbox accounts (2)" in body
        # Sandbox rows render a slate badge per the design doc.
        assert '<span class="badge bg-dark">sandbox</span>' in body
        # Non-sandbox row doesn't bleed into the sandbox section count.
        assert "prod.example" in body  # appears in main activity table


# ---------------------------------------------------------------------------
# Dashboard nav link
# ---------------------------------------------------------------------------


class TestDashboardNavLink:
    def test_standalone_dashboard_links_to_buyer_routing(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert f"/tenant/{standalone_tenant_id}/buyer-routing" in body
        assert "Buyer Routing" in body

    def test_embedded_dashboard_links_to_buyer_routing(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert f"/tenant/{embedded_tenant_id}/buyer-routing" in body
        assert "Buyer Routing" in body
