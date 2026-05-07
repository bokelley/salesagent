"""Unit tests for the AAO checklist gate accepting any resolvable URL.

Before the fix the gate only checked ``tenant.public_agent_url`` (column).
A tenant with that column NULL but a derivable URL (subdomain +
``SALES_AGENT_DOMAIN``, or a ``virtual_host``) was marked incomplete and
blocked from creating media buys, even though publishers could reach it
fine and the admin UI displayed the resolved URL.

This test class drives ``SetupChecklistService._build_aao_tasks`` directly
with stand-in tenant objects (no DB) and confirms the gate now mirrors
:func:`src.services.agent_url_resolver.resolve_agent_url` — the same logic
publisher-partner discovery uses.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.services.setup_checklist_service import SetupChecklistService


def _tenant(
    tenant_id: str = "t1",
    *,
    public_agent_url: str | None = None,
    virtual_host: str | None = None,
    subdomain: str = "test",
    is_embedded: bool = False,
) -> SimpleNamespace:
    """Stand-in for the Tenant ORM model — only the fields the AAO gate reads."""
    return SimpleNamespace(
        tenant_id=tenant_id,
        public_agent_url=public_agent_url,
        virtual_host=virtual_host,
        subdomain=subdomain,
        is_embedded=is_embedded,
    )


class TestAaoChecklistGateUsesResolvedUrl:
    """Open-instance: any resolvable URL marks the item complete."""

    def test_explicit_public_agent_url_marks_complete(self):
        service = SetupChecklistService("t1")
        tenant = _tenant(public_agent_url="https://acme-vanity.example.com")

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "ignored.example.com"}):
            tasks = service._build_aao_tasks(tenant)

        assert len(tasks) == 1
        assert tasks[0].is_complete is True
        assert "https://acme-vanity.example.com" in tasks[0].details

    def test_virtual_host_alone_marks_complete(self):
        """Custom-Domain-only tenants used to fail the gate; now pass."""
        service = SetupChecklistService("t1")
        tenant = _tenant(virtual_host="acme-custom.example.com")

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "ignored.example.com"}):
            tasks = service._build_aao_tasks(tenant)

        assert tasks[0].is_complete is True
        assert "https://acme-custom.example.com" in tasks[0].details

    def test_subdomain_default_marks_complete(self):
        """The Wonderstruck-shape regression — subdomain + SALES_AGENT_DOMAIN
        derive a working URL, gate now accepts it. Was blocking live
        storyboard runs before this fix.
        """
        service = SetupChecklistService("tenant_wonderstruck")
        tenant = _tenant(subdomain="wonderstruck")

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            tasks = service._build_aao_tasks(tenant)

        assert tasks[0].is_complete is True
        assert "https://wonderstruck.sales-agent.scope3.com" in tasks[0].details

    def test_no_resolvable_url_marks_incomplete(self):
        """Genuinely unconfigured open-instance tenant — the gate should
        still block, with a useful action_url pointing at the Account screen."""
        service = SetupChecklistService("t1")
        tenant = _tenant(subdomain="orphan")

        # Force-clear SALES_AGENT_DOMAIN so the platform default doesn't kick in.
        import os

        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("SALES_AGENT_DOMAIN", None)
            tasks = service._build_aao_tasks(tenant)

        assert tasks[0].is_complete is False
        assert tasks[0].action_url == "/tenant/t1/settings#account"


class TestAaoChecklistGateEmbeddedSemantics:
    """Embedded tenants keep strict semantics: only explicit column counts."""

    def test_embedded_with_explicit_url_hides_item(self):
        """Sprint 1.8 §6 — managed tenants with the field set don't see this
        item at all (platform owns it)."""
        service = SetupChecklistService("t_embed")
        tenant = _tenant(
            is_embedded=True,
            public_agent_url="https://acme.host-platform.example",
        )

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            tasks = service._build_aao_tasks(tenant)

        assert tasks == []

    def test_embedded_without_explicit_url_stays_incomplete(self):
        """Even with subdomain or SALES_AGENT_DOMAIN set, embedded tenants
        with NULL public_agent_url stay incomplete — the platform is
        supposed to write the column explicitly per Sprint 1.8 §6.
        Auto-deriving here would mask a real platform-config gap."""
        service = SetupChecklistService("t_embed")
        tenant = _tenant(is_embedded=True, subdomain="acme")

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            tasks = service._build_aao_tasks(tenant)

        assert len(tasks) == 1
        assert tasks[0].is_complete is False
        assert tasks[0].action_url is None  # publisher can't fix this themselves
        assert "host product" in tasks[0].details.lower()

    def test_embedded_with_virtual_host_only_stays_incomplete(self):
        """Same — virtual_host on an embedded row isn't a public URL and
        must not satisfy the gate."""
        service = SetupChecklistService("t_embed")
        tenant = _tenant(is_embedded=True, virtual_host="acme.host.example")

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            tasks = service._build_aao_tasks(tenant)

        assert tasks[0].is_complete is False
