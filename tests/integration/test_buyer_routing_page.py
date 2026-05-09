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
    GamAdvertiser,
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
            GamAdvertiser,
            PropertyTag,
            Principal,
        ):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _add_gam_advertiser(
    tenant_id: str,
    advertiser_id: str,
    name: str,
    *,
    status: str = "active",
) -> None:
    """Seed a row in the synced ``gam_advertisers`` cache for picker /
    name-resolution / validation tests."""
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        session.add(
            GamAdvertiser(
                tenant_id=tenant_id,
                advertiser_id=advertiser_id,
                name=name,
                status=status,
                currency_code="USD",
            )
        )
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
                platform_mappings=fields.get("platform_mappings", {"google_ad_manager": {"advertiser_id": "adv_99"}}),
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

    def test_subnav_present(self, client, standalone_tenant_id):
        """The persistent tenant subnav (with Configure dropdown active)
        replaces the old breadcrumb on this page."""
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert 'class="sa-tenant-nav"' in body
        # Configure trigger is active on configure-namespace pages.
        assert 'class="sa-menu__trigger is-active"' in body
        # Buyer Routing link is in the Configure dropdown panel.
        assert "/buyer-routing" in body

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

    def test_add_rule_button_enabled(self, client, standalone_tenant_id):
        """Sprint 5 workstream C: Add rule button is enabled and wired
        to the in-page modal (no longer the disabled placeholder)."""
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "+ Add rule" in body
        assert 'data-testid="add-rule-btn"' in body
        assert 'data-action="open-add-rule"' in body

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

    def test_promote_button_enabled(self, client, standalone_tenant_id):
        """Sprint 5 workstream E: every activity row has a wired
        Promote button (no longer the disabled placeholder)."""
        _add_account(standalone_tenant_id, operator="promote.example")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "Promote ↑" in body
        assert 'data-action="promote-row"' in body

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


# ---------------------------------------------------------------------------
# Sprint 5 Workstream C — advertiser-name resolution from the cache
# ---------------------------------------------------------------------------


class TestAdvertiserNameResolution:
    """Rules + activity + default sections all render
    ``Name (id)`` when the cache has the advertiser, fall back to the
    raw id when it doesn't, and strike-through inactive rows."""

    def test_rule_renders_advertiser_name_from_cache(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "adv_111", "Scope3-WPP-Sprite")
        _add_routing_rule(standalone_tenant_id, gam_advertiser_id="adv_111")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "Scope3-WPP-Sprite" in body
        assert "adv_111" in body

    def test_rule_falls_back_to_raw_id_when_cache_empty(self, client, standalone_tenant_id):
        # Sync hasn't happened — id renders raw, no name.
        _add_routing_rule(standalone_tenant_id, gam_advertiser_id="adv_uncached")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "<code>adv_uncached</code>" in body

    def test_inactive_advertiser_renders_strikethrough_and_chip(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "adv_dead", "Soft-Deleted Advertiser", status="inactive")
        _add_routing_rule(standalone_tenant_id, gam_advertiser_id="adv_dead")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "br-advertiser-inactive" in body
        assert "Soft-Deleted Advertiser" in body
        assert "br-advertiser-inactive-chip" in body

    def test_default_advertiser_uses_name_when_cache_has_it(self, client, standalone_tenant_id):
        # standalone_tenant_id ships with default_gam_advertiser_id=adv_default_open
        _add_gam_advertiser(standalone_tenant_id, "adv_default_open", "Scope3-Interchange-1")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "Scope3-Interchange-1" in body


# ---------------------------------------------------------------------------
# Sprint 5 Workstream C — picker JSON endpoint
# ---------------------------------------------------------------------------


class TestAdvertiserSearchEndpoint:
    """``GET /tenant/<id>/buyer-routing/api/advertisers`` is the
    session-authenticated mirror of ``GET /gam/advertisers`` used by the
    in-page picker."""

    def test_unfiltered_returns_advertisers_in_name_order(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "1", "Charlie")
        _add_gam_advertiser(standalone_tenant_id, "2", "Alpha")
        _add_gam_advertiser(standalone_tenant_id, "3", "Bravo")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing/api/advertisers")
        assert resp.status_code == 200
        body = resp.get_json()
        assert [a["name"] for a in body["advertisers"]] == ["Alpha", "Bravo", "Charlie"]

    def test_q_substring_is_case_insensitive(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "1", "Acme Sports")
        _add_gam_advertiser(standalone_tenant_id, "2", "Acme Toys")
        _add_gam_advertiser(standalone_tenant_id, "3", "Other")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing/api/advertisers?q=ACme")
        body = resp.get_json()
        assert {a["id"] for a in body["advertisers"]} == {"1", "2"}

    def test_q_numeric_is_exact_id_match(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "1001", "Acme")
        _add_gam_advertiser(standalone_tenant_id, "10010", "Acme Two")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing/api/advertisers?q=1001")
        body = resp.get_json()
        assert len(body["advertisers"]) == 1
        assert body["advertisers"][0]["id"] == "1001"

    def test_q_under_two_chars_is_unfiltered(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "1", "Apple")
        _add_gam_advertiser(standalone_tenant_id, "2", "Banana")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing/api/advertisers?q=A")
        body = resp.get_json()
        assert len(body["advertisers"]) == 2

    def test_unknown_tenant_returns_404(self, client, integration_db):
        resp = client.get("/tenant/no_such_tenant/buyer-routing/api/advertisers")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sprint 5 Workstream C — default advertiser save
# ---------------------------------------------------------------------------


class TestDefaultAdvertiserSave:
    def test_save_succeeds_when_advertiser_in_cache(self, client, unactivated_tenant_id):
        _add_gam_advertiser(unactivated_tenant_id, "adv_new", "New Default")
        resp = client.patch(
            f"/tenant/{unactivated_tenant_id}/buyer-routing/api/default-advertiser",
            json={"default_gam_advertiser_id": "adv_new"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["default_gam_advertiser_id"] == "adv_new"
        # And the next page render reflects the change + drops the banner.
        page = client.get(f"/tenant/{unactivated_tenant_id}/buyer-routing")
        body = page.get_data(as_text=True)
        assert "New Default" in body
        assert 'data-testid="not-activated-banner"' not in body

    def test_save_accepts_when_cache_empty_graceful(self, client, unactivated_tenant_id):
        # Empty cache = onboarding flow, accept the id (mirrors POST /buyer-advertiser-mappings).
        resp = client.patch(
            f"/tenant/{unactivated_tenant_id}/buyer-routing/api/default-advertiser",
            json={"default_gam_advertiser_id": "adv_cold"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_save_rejects_unknown_id_when_cache_populated(self, client, unactivated_tenant_id):
        _add_gam_advertiser(unactivated_tenant_id, "adv_real", "Known")
        resp = client.patch(
            f"/tenant/{unactivated_tenant_id}/buyer-routing/api/default-advertiser",
            json={"default_gam_advertiser_id": "adv_ghost"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "invalid_advertiser_id"

    def test_save_rejects_empty_body(self, client, standalone_tenant_id):
        resp = client.patch(
            f"/tenant/{standalone_tenant_id}/buyer-routing/api/default-advertiser",
            json={"default_gam_advertiser_id": ""},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_default_advertiser"


# ---------------------------------------------------------------------------
# Sprint 5 Workstream C — routing-rule CRUD via session-authenticated endpoints
# ---------------------------------------------------------------------------


class TestRoutingRuleCreate:
    def _post(self, client, tid, **fields):
        body = {
            "operator_domain": fields.get("operator_domain", "wpp.com"),
            "gam_advertiser_id": fields.get("gam_advertiser_id", "adv_pick"),
        }
        if "principal_id" in fields:
            body["principal_id"] = fields["principal_id"]
        if "brand_house" in fields:
            body["brand_house"] = fields["brand_house"]
        if "brand_id" in fields:
            body["brand_id"] = fields["brand_id"]
        return client.post(
            f"/tenant/{tid}/buyer-routing/api/rules",
            json=body,
        )

    def test_create_succeeds(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "adv_pick", "Picked")
        resp = self._post(
            client,
            standalone_tenant_id,
            operator_domain="wpp.com",
            brand_house="coca-cola.com",
            brand_id="sprite",
            gam_advertiser_id="adv_pick",
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["operator_domain"] == "wpp.com"
        assert body["gam_advertiser_id"] == "adv_pick"

    def test_create_rejects_brand_id_without_brand_house(self, client, standalone_tenant_id):
        resp = self._post(
            client,
            standalone_tenant_id,
            operator_domain="wpp.com",
            brand_id="sprite",  # without brand_house
            gam_advertiser_id="adv_pick",
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "brand_house_required"

    def test_create_rejects_unknown_advertiser_when_cache_populated(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "adv_real", "Known")
        resp = self._post(
            client,
            standalone_tenant_id,
            operator_domain="wpp.com",
            gam_advertiser_id="adv_ghost",
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_advertiser_id"

    def test_create_409_on_duplicate(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "adv_pick", "Picked")
        first = self._post(
            client,
            standalone_tenant_id,
            operator_domain="wpp.com",
            gam_advertiser_id="adv_pick",
        )
        assert first.status_code == 201
        second = self._post(
            client,
            standalone_tenant_id,
            operator_domain="wpp.com",
            gam_advertiser_id="adv_pick",
        )
        assert second.status_code == 409
        assert second.get_json()["error"] == "routing_rule_duplicate"


class TestRoutingRulePatch:
    def test_patch_updates_brand_house(self, client, standalone_tenant_id):
        _add_gam_advertiser(standalone_tenant_id, "adv_x", "X")
        rule_id = _add_routing_rule(standalone_tenant_id, operator_domain="wpp.com", gam_advertiser_id="adv_x")
        resp = client.patch(
            f"/tenant/{standalone_tenant_id}/buyer-routing/api/rules/{rule_id}",
            json={"brand_house": "coca-cola.com"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["brand_house"] == "coca-cola.com"

    def test_patch_404_on_unknown_rule(self, client, standalone_tenant_id):
        resp = client.patch(
            f"/tenant/{standalone_tenant_id}/buyer-routing/api/rules/rule_missing",
            json={"brand_house": "x.com"},
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "routing_rule_not_found"

    def test_edit_modal_disables_operator_domain_input(self, client, standalone_tenant_id):
        """The Edit modal must mark the operator_domain input read-only —
        natural-key changes go DELETE+POST. We render the disabling logic
        client-side via JS, but the modal HTML carries the helper text
        explaining the rule + the form input is structurally present."""
        _add_routing_rule(standalone_tenant_id, operator_domain="wpp.com")
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # Helper copy that documents the constraint.
        assert "Operator domain is immutable on edit" in body
        # And the form has an input bound to the operator_domain field.
        assert "data-rule-form-operator-domain" in body


class TestRoutingRuleDelete:
    def test_delete_succeeds(self, client, standalone_tenant_id):
        rule_id = _add_routing_rule(standalone_tenant_id, operator_domain="wpp.com")
        resp = client.delete(f"/tenant/{standalone_tenant_id}/buyer-routing/api/rules/{rule_id}")
        assert resp.status_code == 204
        # Subsequent DELETE for the same id returns 404 (graceful — UI
        # treats it as "already gone").
        resp2 = client.delete(f"/tenant/{standalone_tenant_id}/buyer-routing/api/rules/{rule_id}")
        assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Sprint 5 Workstream C — Add Rule modal HTML shape
# ---------------------------------------------------------------------------


class TestAddRuleModalHtml:
    def test_modal_present_with_required_fields(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert 'data-testid="rule-modal"' in body
        assert "data-rule-form-operator-domain" in body
        assert "data-rule-form-brand-house" in body
        assert "data-rule-form-brand-id" in body
        assert "data-rule-form-gam-advertiser-id" in body

    def test_standalone_tenant_modal_includes_agent_field(self, client, standalone_tenant_id):
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert "data-rule-form-principal-id" in body
        # Label copy explicitly names the field "Agent (principal_id)".
        assert "Agent (principal_id)" in body

    def test_embedded_tenant_modal_hides_agent_field(self, client, embedded_tenant_id):
        resp = client.get(f"/tenant/{embedded_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # No <input> for agent in the modal when embedded — host is the only buyer.
        # (The JS source-block still references the data-attr name as a
        # selector, but no actual input element carries it.)
        assert '<input type="text" data-rule-form-principal-id' not in body
        assert "Agent (principal_id)" not in body


# ---------------------------------------------------------------------------
# Sprint 5 Workstream E — Promote action prefills the modal
# ---------------------------------------------------------------------------


class TestPromoteFromActivity:
    def test_activity_row_carries_data_attributes_for_prefill(self, client, standalone_tenant_id):
        """The Promote button doesn't navigate or POST — it pulls fields
        from the row's data-* attributes and feeds them into the same
        Add Rule modal. Asserting the data attributes are rendered is
        the contract: JS depends on them, and changing them silently
        would break promotion."""
        _add_account(
            standalone_tenant_id,
            operator="wpp.com",
            brand={"domain": "coca-cola.com", "brand_id": "sprite"},
            resolved_via="default",
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        assert 'data-activity-operator-domain="wpp.com"' in body
        assert 'data-activity-brand-house="coca-cola.com"' in body
        assert 'data-activity-brand-id="sprite"' in body
        # Promote button is wired to the action handler.
        assert 'data-action="promote-row"' in body

    def test_promote_button_present_on_every_activity_row(self, client, standalone_tenant_id):
        """Promote is enabled on every row (not just amber fall-throughs) —
        publishers might want to over-route an already-matched buyer to a
        different advertiser."""
        _add_account(
            standalone_tenant_id,
            account_id="acct_amber",
            operator="amber.example",
            resolved_via="default",
        )
        _add_account(
            standalone_tenant_id,
            account_id="acct_green",
            operator="green.example",
            resolved_via="exact",
        )
        resp = client.get(f"/tenant/{standalone_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # One Promote button per row — count buttons (not selector mentions
        # in the JS source-block, which adds a fixed +1).
        assert body.count('<button class="br-btn-secondary" data-action="promote-row"') == 2

    def test_embedded_promote_carries_no_agent_in_data_attrs(self, client, embedded_tenant_id):
        """In embedded mode the Activity table doesn't render agent;
        the promote prefill therefore can't include a principal_id —
        the data attribute is still emitted but stays empty so the
        same JS works for both tenant types."""
        _add_account(embedded_tenant_id, operator="emb.example")
        resp = client.get(f"/tenant/{embedded_tenant_id}/buyer-routing")
        body = resp.get_data(as_text=True)
        # The activity row is still tagged so JS can find it.
        assert "data-activity-row" in body
        # But the principal_id attribute is empty (no agent column in embedded).
        assert 'data-activity-principal-id=""' in body
