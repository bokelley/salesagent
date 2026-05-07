"""Resolve a tenant's public agent URL from its configuration.

Single source of truth for the resolution chain that turns a Tenant row
into the URL publishers list in their ``adagents.json``. Used by:

* ``src.admin.blueprints.publisher_partners`` — match incoming
  ``adagents.json`` entries against this tenant's URL.
* ``src.services.setup_checklist_service`` — gate the "Public Agent URL"
  checklist task on whether *some* URL resolves, not just the explicit
  column.

Keeping these in sync is load-bearing: if the gate is stricter than what
the rest of the system uses to advertise the tenant, a tenant with a
working derived URL gets blocked from creating media buys despite being
fully reachable.

Resolution chain (open-instance):

1. ``tenant.public_agent_url`` (explicit, post-Sprint 1.7)
2. ``https://{tenant.virtual_host}`` (custom domain)
3. ``https://{tenant.subdomain}.{SALES_AGENT_DOMAIN}`` (platform default)

Embedded tenants intentionally do NOT auto-derive — the platform (Scope3)
owns ``public_agent_url`` per Sprint 1.8 §6 and writes it explicitly when
the host product wires the slot. An embedded tenant with NULL stays
incomplete so the gap surfaces to the host product via the §7
``setup_tasks`` scope=platform annotation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.domain_config import get_tenant_url

if TYPE_CHECKING:
    from src.core.database.models import Tenant


def resolve_agent_url(tenant: Tenant) -> str | None:
    """Return the URL publishers list in their ``adagents.json``, or None.

    For embedded tenants, only the explicit ``public_agent_url`` counts
    (see module docstring). For open-instance tenants, falls back through
    ``virtual_host`` → platform-prefixed default.

    Returns ``None`` only when no URL can be derived (e.g. self-hosted
    tenant with no subdomain, no virtual_host, and no explicit URL — or
    embedded tenant whose platform hasn't written the column yet).
    """
    if tenant.public_agent_url:
        return tenant.public_agent_url

    if tenant.is_embedded:
        # Embedded model: platform owns the URL explicitly. Don't derive
        # from subdomain — the embedded tenant's "subdomain" is a routing
        # key on the host platform, not a publicly resolvable hostname.
        return None

    if tenant.virtual_host:
        return f"https://{tenant.virtual_host}"

    return get_tenant_url(tenant.subdomain)
