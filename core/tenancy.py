"""Tenant resolution backed by the existing ``tenants`` table.

The framework ships ``InMemorySubdomainTenantRouter`` for dev; production
adopters back the ``SubdomainTenantRouter`` Protocol with their own table.
We back it with the salesagent ``Tenant`` ORM model, sharing the schema
with the legacy ``src/`` tree.

Replaces ``src/core/domain_routing.py`` (~250 LOC) and the nginx Host-header
routing layer wholesale. Starlette middleware does what nginx was doing.

Skeleton.
"""

from __future__ import annotations


# from adcp.server import SubdomainTenantRouter, Tenant  # adcp>=4.3
# from src.core.database.database_session import get_db_session
# from src.core.database.models import Tenant as TenantRow


# class DbSubdomainTenantRouter:
#     """Resolve incoming Host header against the tenants table.
#
#     Looks up by ``Tenant.subdomain``; the ext payload threads through to
#     ``ToolContext.tenant_id`` so downstream stores filter scope without
#     explicit plumbing.
#     """
#
#     async def resolve(self, host: str) -> Tenant | None:
#         subdomain = host.split(".", 1)[0]
#         with get_db_session() as session:
#             row = session.scalars(
#                 select(TenantRow).filter_by(subdomain=subdomain, is_active=True)
#             ).first()
#         if row is None:
#             return None
#         return Tenant(id=row.tenant_id, display_name=row.name, ext={"row": row})
