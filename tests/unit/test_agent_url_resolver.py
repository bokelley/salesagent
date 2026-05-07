"""Unit tests for ``src.services.agent_url_resolver``.

The resolver is the single source of truth shared by:

* the publisher-partners admin path (matches incoming ``adagents.json``)
* the setup-checklist gate (decides whether a tenant has a usable URL)

Both must agree on what counts as "the tenant has a public URL". A drift
between them is exactly what caused live storyboard runs to be blocked
on the Wonderstruck tenant: the display fell back to a derived URL,
publisher-partners matched against it fine, but the checklist said
incomplete because it only checked the explicit column.

Covers the resolution chain documented in the module:

  open-instance: public_agent_url → virtual_host → subdomain+SALES_AGENT_DOMAIN
  embedded:      public_agent_url ONLY (platform-owned per Sprint 1.8 §6)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.services.agent_url_resolver import resolve_agent_url


def _tenant(
    *,
    public_agent_url: str | None = None,
    virtual_host: str | None = None,
    subdomain: str = "test",
    is_embedded: bool = False,
) -> SimpleNamespace:
    """Stand-in for the Tenant ORM model — only the fields the resolver reads."""
    return SimpleNamespace(
        public_agent_url=public_agent_url,
        virtual_host=virtual_host,
        subdomain=subdomain,
        is_embedded=is_embedded,
    )


class TestResolveAgentUrlOpenInstance:
    """Open-instance tenants: full chain (explicit → virtual_host → subdomain)."""

    def test_explicit_public_agent_url_wins(self):
        tenant = _tenant(
            public_agent_url="https://acme-vanity.example.com",
            virtual_host="ignored.example.com",
            subdomain="ignored",
        )
        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "ignored.example.com"}):
            assert resolve_agent_url(tenant) == "https://acme-vanity.example.com"

    def test_falls_back_to_virtual_host(self):
        """No explicit URL but a Custom Domain (virtual_host) is set."""
        tenant = _tenant(virtual_host="acme-custom.example.com", subdomain="ignored")
        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "ignored.example.com"}):
            assert resolve_agent_url(tenant) == "https://acme-custom.example.com"

    def test_falls_back_to_subdomain_default(self):
        """Wonderstruck case — no explicit URL, no virtual_host, but
        subdomain + SALES_AGENT_DOMAIN derive a working URL. Used to be
        rejected by the setup gate; now correctly returned."""
        tenant = _tenant(subdomain="wonderstruck")
        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            assert resolve_agent_url(tenant) == "https://wonderstruck.sales-agent.scope3.com"

    def test_returns_none_when_nothing_resolves(self):
        """No public_agent_url, no virtual_host, no SALES_AGENT_DOMAIN —
        nothing publishers could list. Tenant is genuinely incomplete."""
        tenant = _tenant(subdomain="orphan")
        with patch.dict("os.environ", {}, clear=False):
            # Force-clear in case of inheritance from outer test config.
            import os

            os.environ.pop("SALES_AGENT_DOMAIN", None)
            assert resolve_agent_url(tenant) is None


class TestResolveAgentUrlEmbedded:
    """Embedded tenants: ONLY the explicit column counts.

    Per Sprint 1.8 §6 the platform owns ``public_agent_url`` for embedded
    tenants. The "subdomain" on an embedded row is a routing key on the
    host platform, not a publicly resolvable hostname — auto-deriving
    would point publishers at a URL that doesn't exist.
    """

    def test_explicit_public_agent_url_returned(self):
        tenant = _tenant(
            is_embedded=True,
            public_agent_url="https://acme.host-platform.example",
            subdomain="ignored",
        )
        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            assert resolve_agent_url(tenant) == "https://acme.host-platform.example"

    def test_does_not_derive_from_subdomain(self):
        """An embedded tenant with subdomain set but no public_agent_url
        must NOT auto-derive — that would hide a real platform-config gap."""
        tenant = _tenant(is_embedded=True, subdomain="acme")
        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            assert resolve_agent_url(tenant) is None

    def test_does_not_derive_from_virtual_host(self):
        """Same — virtual_host on an embedded row isn't a public URL."""
        tenant = _tenant(is_embedded=True, virtual_host="acme.host.example")
        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            assert resolve_agent_url(tenant) is None
