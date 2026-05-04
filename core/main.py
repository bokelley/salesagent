"""Entrypoint: build PlatformRouter, attach middleware, call serve().

Replaces ``src/core/main.py`` (MCP) and ``src/a2a_server/adcp_a2a_server.py``
with a single ``serve()`` call. One process, two transports if needed.

Skeleton — wired after v3→v4 migration completes.
"""

from __future__ import annotations


def build_app():
    """Construct the ASGI app: tenancy middleware → PlatformRouter → serve().

    Pseudocode::

        from adcp.decisioning import PlatformRouter, serve
        from adcp.server import SubdomainTenantMiddleware
        from core.platforms.mock import MockSellerPlatform
        from core.stores.tenants import db_backed_tenant_router
        from core.stores.accounts import SalesagentAccountStore
        from core.auth import build_auth_middleware

        router = PlatformRouter(
            accounts=SalesagentAccountStore(),
            platforms=load_platforms_from_db(),  # one DecisioningPlatform per tenant
            capabilities=union_of_child_capabilities(),
        )

        return serve(
            router,
            transport="mcp",
            middleware=[
                SubdomainTenantMiddleware(router=db_backed_tenant_router()),
                build_auth_middleware(),
            ],
            test_controller=optional_storyboard_controller(),
        )
    """
    raise NotImplementedError("M1 wiring lands after v3→v4 migration")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=8080)
