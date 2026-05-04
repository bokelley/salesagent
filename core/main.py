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
    BearerTokenAuthMiddleware,
    CallableSubdomainTenantRouter,
    Principal,
    SubdomainTenantMiddleware,
    Tenant,
    auth_context_factory,
)
from sqlalchemy import select

from core.platforms.gam import WonderstruckGamPlatform
from core.platforms.mock import MockSellerPlatform
from core.stores.accounts import SalesagentAccountStore
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow
from src.core.database.models import Tenant as TenantRow

logger = logging.getLogger(__name__)


# ---- Tenant resolution (uses adcp PR #544 CallableSubdomainTenantRouter) ----


async def _resolve_tenant(host: str) -> Tenant | None:
    """Map a normalized host (lower-cased, port-stripped) to a Tenant.

    Strips ``.localhost`` / ``.example.com`` suffix to get the subdomain,
    then looks up the matching active row in the ``tenants`` table.
    Production deployments add their actual base domain to this matcher.
    """
    # Strip known dev/prod suffixes; whatever's left is the subdomain.
    subdomain = host
    for suffix in (".localhost", ".example.com"):
        if subdomain.endswith(suffix):
            subdomain = subdomain[: -len(suffix)]
            break
    if not subdomain or subdomain == host:
        # No recognized suffix → host has no tenant prefix
        return None

    with get_db_session() as session:
        row = session.scalars(
            select(TenantRow).filter_by(subdomain=subdomain, is_active=True)
        ).first()

    if row is None:
        return None
    return Tenant(id=row.tenant_id, display_name=row.name)


def build_subdomain_router() -> CallableSubdomainTenantRouter:
    """Create the tenant router with a 60-second cache.

    The cache is small but bounded — tenants change infrequently relative
    to request volume, so even a low-traffic seller hits the cache often
    enough to benefit. ``invalidate(host)`` is called from the admin
    flow when a tenant is created / deactivated / has its subdomain
    rotated (M2 wiring).
    """
    return CallableSubdomainTenantRouter(
        _resolve_tenant,
        cache_size=512,
        cache_ttl_seconds=60.0,
    )


# ---- Token auth (uses adcp PR #545 BearerTokenAuthMiddleware kwargs) -------


def _validate_token(token: str) -> Principal | None:
    """Resolve an ``x-adcp-auth`` token to the matching :class:`Principal`.

    Looks up ``Principal.access_token`` in the existing salesagent
    schema. Returns ``None`` (NOT raises) on miss — see the
    ``BearerTokenAuthMiddleware`` docstring for the security rationale.

    Memory profile: each call opens a short-lived DB session, queries by
    indexed token column, returns the row. No state retained outside the
    session. Per-request cost; matches the framework's expected shape.
    """
    if not token:
        return None
    with get_db_session() as session:
        row = session.scalars(
            select(PrincipalRow).filter_by(access_token=token)
        ).first()
    if row is None:
        return None
    return Principal(
        caller_identity=row.principal_id,
        tenant_id=row.tenant_id,
    )


# ---- Per-tenant DecisioningPlatform loading -------------------------------


def _load_platforms() -> dict[str, MockSellerPlatform | WonderstruckGamPlatform]:
    """One per-tenant DecisioningPlatform, dispatched by ``tenants.ad_server``.

    - ``ad_server == 'google_ad_manager'`` → :class:`WonderstruckGamPlatform`
      (reads real Placements from the tenant's GAM network)
    - anything else (default ``mock``) → :class:`MockSellerPlatform`
      (reads from the salesagent ``products`` table)
    """
    with get_db_session() as session:
        rows = session.scalars(select(TenantRow).filter_by(is_active=True)).all()

    out: dict[str, MockSellerPlatform | WonderstruckGamPlatform] = {}
    for row in rows:
        if row.ad_server == "google_ad_manager":
            out[row.tenant_id] = WonderstruckGamPlatform()
            logger.info(f"  loaded WonderstruckGamPlatform for tenant {row.tenant_id!r}")
        else:
            out[row.tenant_id] = MockSellerPlatform()
            logger.info(f"  loaded MockSellerPlatform for tenant {row.tenant_id!r}")
    return out


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
    """FastMCP DNS-rebinding allowlist for the dev/prod base domains.

    The framework auto-synthesizes ``host:*`` siblings (PR #537) so we
    only register the bare hosts. Per-tenant subdomain validation
    happens AFTER the allowlist filter, inside our resolver — adding
    the wildcards here would defeat the per-tenant scoping.

    Production adds its actual base domain (e.g.
    ``*.sales-agent.example.com``) by extending this list.
    """
    return [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        # Tenant subdomains — wildcard pattern matches any subdomain
        # under these bases.
        "*.localhost",
        "*.example.com",
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("ADCP_PORT") or os.environ.get("PORT") or 3001)

    router = build_router()
    subdomain_router = build_subdomain_router()

    serve(
        router,
        name="salesagent-core",
        port=port,
        auto_emit_completion_webhooks=False,
        # Auth + tenant resolution chain. Order matters: subdomain runs
        # outermost so unknown hosts 404 before token validation, then
        # token auth populates the principal contextvar that
        # auth_context_factory reads to build ToolContext.
        asgi_middleware=[
            (SubdomainTenantMiddleware, {"router": subdomain_router}),
            (
                BearerTokenAuthMiddleware,
                {
                    "validate_token": _validate_token,
                    "header_name": "x-adcp-auth",
                    "bearer_prefix_required": False,
                },
            ),
        ],
        context_factory=auth_context_factory,
        allowed_hosts=_allowed_hosts(),
    )
