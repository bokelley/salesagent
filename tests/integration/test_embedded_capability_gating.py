"""Sprint 7 Phase 4b — per-section capability gating in Tenant Settings.

When ``MANAGED_INSTANCE=true`` and ``EMBEDDED_CAPABILITIES`` declares a
workflow as ``storefront``-owned, the publisher's settings UI must hide
the section and the POST handler must reject writes with 403. Open
instances (``MANAGED_INSTANCE`` unset) ignore the env var entirely.

Each capability has three tests:
- Open instance: section visible, POST works.
- Embedded + ``publisher``: section visible, POST works.
- Embedded + ``storefront``: section hidden, POST returns 403.

See ``docs/design/embedded-mode-sprint-7-ia-cleanup.md`` Phase 4b.
"""

from __future__ import annotations

import pytest

from tests.integration._embedded_helpers import (
    cleanup_embedded_test_tenant,
    insert_embedded_test_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def test_tenant_id(integration_db):
    """A single open-instance tenant used for every test.

    Capability gating is *instance-level* — driven by the env vars
    ``MANAGED_INSTANCE`` and ``EMBEDDED_CAPABILITIES``, not by
    ``tenant.is_embedded``. We don't need a separate ``is_embedded=True``
    tenant to verify capability gates; we'd just hit the X-Identity
    auth middleware on every request. The session bypass works
    cleanly against an open tenant.
    """
    tid = insert_embedded_test_tenant(is_embedded=False, name_prefix="t_cap")
    yield tid
    cleanup_embedded_test_tenant(tid)


@pytest.fixture
def open_tenant_id(test_tenant_id):
    """Alias for the visibility-on-open-instance test. Same tenant; the
    distinguishing factor is whether ``MANAGED_INSTANCE`` is set."""
    return test_tenant_id


# ---------------------------------------------------------------------------
# Capability scenarios
# ---------------------------------------------------------------------------
#
# Each section's gate is verified by three checks against an embedded
# tenant's Settings page + POST endpoint. The capability name in the
# env var is the JSON key; the marker is a substring guaranteed to be
# present in the rendered HTML when the section is visible.

# Capability gates render across two standalone pages now. Sprint 7 Phase 2
# moved the business-rules subsections (creative_approval /
# advertising_policy / product_ranking / brand_manifest) to
# ``/tenant/<id>/settings/policies/`` and the integrations subsections
# (slack / ai_services / creative_agents / signals_agents) to
# ``/tenant/<id>/settings/integrations/``. Each entry declares the URL
# the test should probe.
CAPABILITY_RENDER_MARKERS = {
    "creative_approval": {
        "url": "/settings/policies/",
        "markers": ("<h3>Approval Workflow</h3>", "<h3>Creative Review</h3>"),
    },
    "advertising_policy": {"url": "/settings/policies/", "markers": ("<h3>Advertising Policy</h3>",)},
    "product_ranking": {"url": "/settings/policies/", "markers": ("<h3>Product Ranking</h3>",)},
    "brand_manifest": {"url": "/settings/policies/", "markers": ("<h3>Brand Manifest Policy</h3>",)},
    "slack": {"url": "/settings/integrations/", "markers": ("<h3>Slack Integration</h3>",)},
    "ai_services": {"url": "/settings/integrations/", "markers": ("<h3>AI Services</h3>",)},
    "creative_agents": {"url": "/settings/integrations/", "markers": ("<h3>Creative Agents</h3>",)},
    "signals_agents": {"url": "/settings/integrations/", "markers": ("<h3>Signals Discovery Agents</h3>",)},
}


@pytest.mark.parametrize("capability,spec", list(CAPABILITY_RENDER_MARKERS.items()))
def test_section_visible_on_open_instance(embedded_client, open_tenant_id, capability, spec):
    """Open instances ignore EMBEDDED_CAPABILITIES — every section renders."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}{spec['url']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for marker in spec["markers"]:
        assert marker in body, f"{capability}: open instance missing {marker!r} at {spec['url']}"


@pytest.mark.parametrize("capability,spec", list(CAPABILITY_RENDER_MARKERS.items()))
def test_section_visible_on_embedded_publisher_owned(monkeypatch, embedded_client, test_tenant_id, capability, spec):
    """Embedded + capability=publisher (default): section still renders."""
    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)

    resp = embedded_client.get(f"/tenant/{test_tenant_id}{spec['url']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for marker in spec["markers"]:
        assert marker in body, f"{capability}: publisher-owned but missing {marker!r}"


@pytest.mark.parametrize("capability,spec", list(CAPABILITY_RENDER_MARKERS.items()))
def test_section_hidden_when_storefront_owned(monkeypatch, embedded_client, test_tenant_id, capability, spec):
    """Embedded + capability=storefront: section is removed from the page."""
    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.setenv("EMBEDDED_CAPABILITIES", f'{{"{capability}": "storefront"}}')

    resp = embedded_client.get(f"/tenant/{test_tenant_id}{spec['url']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for marker in spec["markers"]:
        assert marker not in body, f"{capability}: storefront-owned but {marker!r} still rendered"


def test_policies_page_loads_for_open_tenant(embedded_client, open_tenant_id):
    """Sanity: the new standalone Policies & Workflows page renders."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/policies/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h2>Policies &amp; Workflows</h2>" in body or "Policies & Workflows" in body
    assert '<form id="business-rules-form">' in body


def test_old_business_rules_deep_link_redirects(embedded_client, open_tenant_id):
    """``/settings/business-rules`` was the legacy deep-link before
    Phase 2 promoted the section out. Redirect to the new standalone
    page so bookmarks don't silently land on the default Account tab."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/business-rules", follow_redirects=False)
    assert resp.status_code == 302
    assert f"/tenant/{open_tenant_id}/settings/policies/" in resp.headers["Location"]


def test_no_slash_policies_deep_link_redirects(embedded_client, open_tenant_id):
    """``/settings/policies`` (no trailing slash) was ambiguous before:
    it could match the legacy ``<section>`` route and silently render
    Tenant Settings with no matching tab. Flask's strict-slash auto-
    redirect (308) gets there first because the canonical
    ``/settings/policies/`` is registered with a trailing slash —
    either way the user lands on the standalone page."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/policies", follow_redirects=False)
    # 302 (my redirect map) or 308 (Flask strict-slash) — both fine.
    assert resp.status_code in (302, 308)
    assert f"/tenant/{open_tenant_id}/settings/policies/" in resp.headers["Location"]


def test_tenant_settings_no_longer_renders_business_rules_section(embedded_client, open_tenant_id):
    """The in-page section is gone — the tab data-attribute and the
    section's H2 must NOT render in Tenant Settings."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
    body = resp.get_data(as_text=True)
    assert 'data-section="business-rules"' not in body
    assert 'id="business-rules"' not in body


def test_integrations_page_loads_for_open_tenant(embedded_client, open_tenant_id):
    """Sanity: the new standalone Integrations page renders."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/integrations/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Integrations" in body
    # AI Services subsection is the meatiest — confirm at least one
    # marker that proves the lift carried over.
    assert "<h3>AI Services</h3>" in body
    assert "<h3>Slack Integration</h3>" in body


def test_old_integrations_deep_link_redirects(embedded_client, open_tenant_id):
    """``/settings/integrations`` (legacy in-page anchor + the no-slash
    form of the new URL both) redirect to the canonical standalone
    page."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/integrations", follow_redirects=False)
    # 302 from _PROMOTED_SECTION_REDIRECTS or 308 from Flask strict-slash;
    # either lands on the standalone page.
    assert resp.status_code in (302, 308)
    assert f"/tenant/{open_tenant_id}/settings/integrations/" in resp.headers["Location"]


def test_tenant_settings_no_longer_renders_integrations_section(embedded_client, open_tenant_id):
    """The in-page Integrations section is gone — tab + section both."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
    body = resp.get_data(as_text=True)
    assert 'data-section="integrations"' not in body
    assert 'id="integrations"' not in body


# ---------------------------------------------------------------------------
# Phase 3 (#437): Products + Inventory in-page tabs removed
# ---------------------------------------------------------------------------


def test_old_products_deep_link_redirects(embedded_client, open_tenant_id):
    """``/settings/products`` was the legacy in-page anchor. Products lives
    in primary top nav now (#451). Old deep-links forward there."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/products", follow_redirects=False)
    assert resp.status_code == 302
    assert f"/tenant/{open_tenant_id}/products/" in resp.headers["Location"]


def test_old_inventory_deep_link_redirects(embedded_client, open_tenant_id):
    """``/settings/inventory`` was the legacy in-page anchor. The GAM sync
    UI moved to /inventory (Configure → Inventory operations → Sync inventory).
    The redirect lands on the canonical page — exact match prevents drift
    onto adjacent paths like /inventory/browse or /inventory-profiles."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings/inventory", follow_redirects=False)
    assert resp.status_code == 302
    # Match the path component exactly (allow scheme+host prefix from Flask).
    assert resp.headers["Location"].endswith(f"/tenant/{open_tenant_id}/inventory")


def test_tenant_settings_no_longer_renders_products_or_inventory_sections(embedded_client, open_tenant_id):
    """Sprint 7 Phase 3 (#437): in-page Products + Inventory tabs are gone
    — rail entries (data-section=...) and content (<div id=...) both."""
    resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
    body = resp.get_data(as_text=True)
    assert 'data-section="products"' not in body
    assert 'data-section="inventory"' not in body
    # The standalone Products and Inventory pages own those IDs now —
    # checking that the in-page section divs are gone from /settings.
    # (Both <div id="products"> and <div id="inventory"> previously
    # appeared inside tenant_settings.html.)
    assert '<div id="products" class="settings-section"' not in body
    assert '<div id="inventory" class="settings-section"' not in body


# ---------------------------------------------------------------------------
# POST handler 403 enforcement (defense-in-depth)
# ---------------------------------------------------------------------------


class TestSlackPostGated:
    """``settings.update_slack`` rejects writes when slack is storefront-owned."""

    def test_post_succeeds_when_publisher_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/slack",
            data={"slack_webhook_url": "", "slack_audit_webhook_url": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 302  # success → redirect to settings

    def test_post_returns_403_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "storefront"}')
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/slack",
            data={"slack_webhook_url": "https://hooks.slack.com/services/A/B/C"},
        )
        assert resp.status_code == 403
        assert b"slack" in resp.data.lower()


class TestAiServicesPostGated:
    """``settings.update_ai`` and probes reject writes when ai_services is
    storefront-owned."""

    def test_update_ai_returns_403_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"ai_services": "storefront"}')
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/ai",
            data={"ai_provider": "gemini", "ai_model": "gemini-2.0-flash"},
        )
        assert resp.status_code == 403

    def test_get_ai_models_returns_403_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"ai_services": "storefront"}')
        resp = embedded_client.get(f"/tenant/{test_tenant_id}/settings/ai/models")
        assert resp.status_code == 403


class TestBusinessRulesPostGated:
    """``settings.update_business_rules`` rejects a write that touches any
    storefront-owned field, even though the route handles multiple
    capabilities. Currency limits and naming templates remain
    publisher-owned and POST through this route stays writable when only
    those fields are submitted."""

    def test_creative_approval_field_rejected_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "storefront"}')
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/business-rules",
            data={"approval_mode": "auto-approve"},
        )
        assert resp.status_code == 403

    def test_advertising_policy_field_rejected_when_storefront_owned(
        self, monkeypatch, embedded_client, test_tenant_id
    ):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"advertising_policy": "storefront"}')
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/business-rules",
            data={"policy_check_enabled": "on"},
        )
        assert resp.status_code == 403

    def test_product_ranking_field_rejected_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"product_ranking": "storefront"}')
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/business-rules",
            data={"product_ranking_prompt": "rank by relevance"},
        )
        assert resp.status_code == 403

    def test_publisher_fields_still_writable_when_only_one_capability_storefront_owned(
        self, monkeypatch, embedded_client, test_tenant_id
    ):
        """If creative_approval is storefront-owned but the publisher posts
        only currency/naming fields, the write succeeds. Defense-in-depth
        guards specific fields, not the whole route."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "storefront"}')
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/business-rules",
            data={
                "order_name_template": "{promoted_offering} - {date_range}",
                "line_item_name_template": "{order_name} - {product_name}",
            },
        )
        assert resp.status_code == 302  # success → redirect

    def test_brand_manifest_storefront_does_not_break_naming_only_post(
        self, monkeypatch, embedded_client, test_tenant_id
    ):
        """Regression for the code-reviewer's #1 blocker: when
        ``brand_manifest`` is storefront-owned, the template must hide
        the dropdown so the form doesn't auto-submit
        ``brand_manifest_policy`` and trigger a false 403 on otherwise-
        innocuous saves.

        Pre-fix bug: the brand-manifest section was unconditionally
        rendered. Its ``<select>`` always submitted a value with the
        business-rules form. The capability gate saw the field, matched
        ``brand_manifest=storefront``, and 403d every save."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"brand_manifest": "storefront"}')

        # Render the form on the embedded settings page. The brand-manifest
        # dropdown must NOT render — otherwise the publisher's normal save
        # would always 403.
        resp = embedded_client.get(f"/tenant/{test_tenant_id}/settings")
        body = resp.get_data(as_text=True)
        assert 'name="brand_manifest_policy"' not in body

        # Posting only naming-template fields (i.e., not brand_manifest_policy)
        # must succeed — proves the gate is correctly scoped.
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/business-rules",
            data={"order_name_template": "{promoted_offering}"},
        )
        assert resp.status_code == 302


class TestGeneralSettingsPostGated:
    """``settings.update_general`` must not silently clobber storefront-
    owned ``enable_axe_signals`` / ``human_review_required`` to False on
    every save. Security review H1."""

    def test_enable_axe_signals_preserved_when_signals_agents_storefront_owned(
        self, monkeypatch, embedded_client, test_tenant_id
    ):
        """``update_general``'s ``checkbox-absent means False`` logic
        would clobber the storefront-owned field on every save. After
        the gate, the field must be left alone."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        # Seed: tenant.enable_axe_signals = True
        with get_db_session() as session:
            session.info["management_api_caller"] = True
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=test_tenant_id)).first()
            tenant.enable_axe_signals = True
            session.commit()

        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"signals_agents": "storefront"}')

        # Submit a /general POST that does NOT include enable_axe_signals
        # (a normal tenant-name edit).
        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/general",
            data={"name": "Renamed Tenant"},
        )
        # The route may redirect (302) on success — the key check is the field.
        assert resp.status_code in (200, 302)

        # The storefront-owned field must still be True.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=test_tenant_id)).first()
            assert tenant.enable_axe_signals is True, (
                "update_general silently clobbered storefront-owned enable_axe_signals to False — H1 regression"
            )

    def test_human_review_required_preserved_when_creative_approval_storefront_owned(
        self, monkeypatch, embedded_client, test_tenant_id
    ):
        """Same shape: ``human_review_required`` must not flip to False
        on a /general save when ``creative_approval`` is storefront-owned."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        with get_db_session() as session:
            session.info["management_api_caller"] = True
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=test_tenant_id)).first()
            tenant.human_review_required = True
            session.commit()

        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "storefront"}')

        resp = embedded_client.post(
            f"/tenant/{test_tenant_id}/settings/general",
            data={"name": "Renamed Tenant"},
        )
        assert resp.status_code in (200, 302)

        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=test_tenant_id)).first()
            assert tenant.human_review_required is True, (
                "update_general silently clobbered storefront-owned human_review_required to False — H1 regression"
            )


class TestCreativeAgentsBlueprintGated:
    """The creative-agents blueprint's ``before_request`` blocks every
    route when the storefront owns creative_agents."""

    def test_list_page_returns_403_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_agents": "storefront"}')
        resp = embedded_client.get(f"/tenant/{test_tenant_id}/creative-agents/")
        assert resp.status_code == 403

    def test_list_page_renders_when_publisher_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = embedded_client.get(f"/tenant/{test_tenant_id}/creative-agents/")
        # 200 (list page) or 302 (redirect to login if test session lapsed) —
        # the point is NOT 403.
        assert resp.status_code != 403


class TestSignalsAgentsBlueprintGated:
    """Same pattern for the signals-agents blueprint."""

    def test_list_page_returns_403_when_storefront_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"signals_agents": "storefront"}')
        resp = embedded_client.get(f"/tenant/{test_tenant_id}/signals-agents/")
        assert resp.status_code == 403

    def test_list_page_renders_when_publisher_owned(self, monkeypatch, embedded_client, test_tenant_id):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        resp = embedded_client.get(f"/tenant/{test_tenant_id}/signals-agents/")
        assert resp.status_code != 403
