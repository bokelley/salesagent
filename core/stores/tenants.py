"""Tenant lookup for SubdomainTenantMiddleware.

Wraps the existing ``Tenant`` ORM model in the framework's
``SubdomainTenantRouter`` Protocol so the Starlette middleware can resolve
``Host: acme.example.com`` → ``Tenant(id='acme')``.

Skeleton.
"""

from __future__ import annotations


# from sqlalchemy import select
# from adcp.server import SubdomainTenantRouter, Tenant
# from src.core.database.database_session import get_db_session
# from src.core.database.models import Tenant as TenantRow


# class DbSubdomainTenantRouter:
#     async def resolve(self, host: str) -> Tenant | None:
#         subdomain = host.split(".", 1)[0]
#         with get_db_session() as session:
#             row = session.scalars(
#                 select(TenantRow).filter_by(subdomain=subdomain, is_active=True)
#             ).first()
#         if row is None:
#             return None
#         return Tenant(id=row.tenant_id, display_name=row.name)
