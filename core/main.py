"""Entrypoint: build PlatformRouter, attach SubdomainTenantMiddleware, serve.

Replaces ``src/core/main.py`` (MCP) and ``src/a2a_server/adcp_a2a_server.py``
(A2A) with a single ``serve()`` call from the framework. One process,
either transport. No nginx routing — Starlette middleware handles
multi-tenancy via the ``Host`` header.

For development without DNS::

    /etc/hosts:
        127.0.0.1 default.localhost acme.localhost beta.localhost

    PORT=3001 uv run python -m core.main

    # Then connect any AdCP MCP buyer to:
    http://default.localhost:3001/mcp
"""

from __future__ import annotations

import logging
import os

from adcp.decisioning import (
    DecisioningCapabilities,
    PlatformRouter,
    serve,
)
from adcp.decisioning.capabilities import (
    Account as CapabilitiesAccount,
)
from adcp.decisioning.capabilities import (
    Adcp,
    IdempotencySupported,
    MediaBuy,
    SupportedProtocol,
)
from adcp.server import (
    InMemorySubdomainTenantRouter,
    SubdomainTenantMiddleware,
    Tenant,
)
from sqlalchemy import select

from core.platforms.mock import MockSellerPlatform
from core.stores.accounts import SalesagentAccountStore
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant as TenantRow

logger = logging.getLogger(__name__)


def _load_tenant_subdomain_map() -> dict[str, Tenant]:
    """Build the SubdomainTenantMiddleware host map from the DB.

    Each active tenant row contributes ``<subdomain>.localhost`` for
    local dev and ``<subdomain>.example.com`` for production. The
    framework's router lower-cases hosts and strips ``:port`` so a
    single registration covers both ``acme.localhost`` and
    ``acme.localhost:3001``.
    """
    with get_db_session() as session:
        rows = session.scalars(select(TenantRow).filter_by(is_active=True)).all()

    mapping: dict[str, Tenant] = {}
    for row in rows:
        if not row.subdomain:
            continue
        tenant = Tenant(id=row.tenant_id, display_name=row.name)
        for host in (
            f"{row.subdomain}.localhost",
            f"{row.subdomain}.example.com",
        ):
            mapping[host] = tenant
    return mapping


def _load_platforms() -> dict[str, MockSellerPlatform]:
    """One MockSellerPlatform per active tenant.

    M1 wires every tenant to the same mock platform; M3 swaps in
    real adapters (GAM/Kevel) keyed off ``tenants.adapter_type``.
    """
    with get_db_session() as session:
        rows = session.scalars(select(TenantRow).filter_by(is_active=True)).all()
    return {row.tenant_id: MockSellerPlatform() for row in rows}


def build_router() -> PlatformRouter:
    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=86400),
        ),
        account=CapabilitiesAccount(supported_billing=["operator"]),
        media_buy=MediaBuy(supported_pricing_models=["cpm"]),
        supported_protocols=[SupportedProtocol.media_buy],
    )
    return PlatformRouter(
        accounts=SalesagentAccountStore(),
        platforms=_load_platforms(),
        capabilities=capabilities,
    )


def _allowed_hosts() -> list[str]:
    """Loadable subdomains for FastMCP's DNS-rebinding allowlist."""
    hosts: list[str] = []
    for host in _load_tenant_subdomain_map():
        hosts.extend([host, f"{host}:*"])
    return hosts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("ADCP_PORT") or os.environ.get("PORT") or 3001)

    router = build_router()
    subdomain_router = InMemorySubdomainTenantRouter(
        tenants=_load_tenant_subdomain_map()
    )

    serve(
        router,
        name="salesagent-core",
        port=port,
        auto_emit_completion_webhooks=False,
        asgi_middleware=[(SubdomainTenantMiddleware, {"router": subdomain_router})],
        allowed_hosts=_allowed_hosts(),
    )
