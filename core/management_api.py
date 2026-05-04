"""Management API: human/operator-facing CRUD for tenants, principals, tokens.

Replaces ``src/admin/`` Flask UI with a clean FastAPI surface. No HTML, no
Google OAuth — just signed-token authenticated REST endpoints. UI can come
later as a separate frontend that calls this API.

Mounted alongside the AdCP transport endpoints so a single process serves:
- ``/mcp/`` and/or ``/a2a`` — buyer-facing AdCP (via ``serve()``)
- ``/manage/*``               — operator-facing CRUD (this module)
- ``/.well-known/*``          — adagents.json, brand.json (framework-served)

Skeleton.
"""

from __future__ import annotations


# from fastapi import APIRouter, Depends, HTTPException

# router = APIRouter(prefix="/manage")


# @router.get("/tenants")
# async def list_tenants(): ...


# @router.post("/tenants")
# async def create_tenant(): ...


# @router.get("/tenants/{tenant_id}/principals")
# async def list_principals(tenant_id: str): ...


# @router.post("/tenants/{tenant_id}/principals")
# async def create_principal(tenant_id: str): ...


# @router.post("/tenants/{tenant_id}/principals/{principal_id}/tokens")
# async def issue_token(tenant_id: str, principal_id: str): ...
