"""AccountStore over the salesagent ``principals`` and ``tenants`` tables.

Resolution order:
1. Bearer-authenticated principal's ``tenant_id`` (set by
   :class:`BearerTokenAuthMiddleware`) — authenticated buyers always
   operate in their principal's tenant regardless of which Host header
   they arrive under. Critical for embedded mode where one host serves
   many tenants.
2. Subdomain-set contextvar (set by :class:`SubdomainTenantMiddleware`)
   — fallback for unauthenticated discovery (agent card, well-known)
   and traditional one-tenant-per-subdomain deployments.
3. Explicit ``account.account_id`` like ``"tenant-a:acct_demo"`` —
   storyboards/dev.
4. Reject with ``ACCOUNT_NOT_FOUND``.

The resolved Account's ``metadata['tenant_id']`` is what
:class:`PlatformRouter` reads to pick the per-tenant ``DecisioningPlatform``.
"""

from __future__ import annotations

from typing import Any, Literal

from adcp.decisioning import AdcpError
from adcp.decisioning.context import AuthInfo
from adcp.decisioning.types import Account
from adcp.server import current_tenant
from adcp.server.auth import current_principal, current_tenant as auth_current_tenant
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow, Tenant


class SalesagentAccountStore:
    """Tenant-scoped AccountStore backed by the salesagent ORM.

    Reads :class:`Tenant` rows from the existing schema. Bridges to
    :class:`PlatformRouter` by stamping ``metadata['tenant_id']`` on
    every resolved Account.
    """

    resolution: Literal["explicit"] = "explicit"

    def resolve(
        self,
        ref: dict[str, Any] | None = None,
        auth_info: AuthInfo | None = None,
    ) -> Account[dict[str, Any]]:
        tenant_id = (
            self._tenant_from_principal()
            or self._tenant_from_subdomain()
            or self._tenant_from_ref(ref)
        )
        if tenant_id is None or not self._tenant_exists(tenant_id):
            raise AdcpError(
                "ACCOUNT_NOT_FOUND",
                message=(
                    "Could not resolve a tenant. Authenticate via "
                    "x-adcp-auth, send via the tenant subdomain "
                    "(e.g. acme.example.com), or pass account.account_id "
                    "with a 'tenant_id:' prefix."
                ),
                recovery="terminal",
                field="account",
            )

        account_id = (ref or {}).get("account_id") if isinstance(ref, dict) else None
        if not account_id:
            account_id = f"{tenant_id}:default"

        return Account(
            id=account_id,
            metadata={"tenant_id": tenant_id},
            auth_info=_auth_info_to_dict(auth_info),
        )

    @staticmethod
    def _tenant_from_principal() -> str | None:
        """Authenticated principal's tenant — beats subdomain.

        ``BearerTokenAuthMiddleware`` populates two ContextVars on
        successful token validation: ``current_principal`` (the
        ``caller_identity`` string) and the auth-module's own
        ``current_tenant`` (the principal's ``tenant_id``). We prefer
        the latter when present — a request that authenticates as a
        principal in ``tenant_xyz`` belongs to ``tenant_xyz`` even
        when the Host header points elsewhere (embedded mode, single
        ingress fronting many tenants).

        Falls back to a DB lookup keyed on the principal_id if the
        auth module's tenant ContextVar is unset for any reason — a
        defensive belt-and-suspenders pattern, since ``current_tenant``
        in adcp.server.auth is set in lockstep with ``current_principal``
        by the middleware.
        """
        tenant_id = auth_current_tenant.get()
        if tenant_id:
            return tenant_id
        principal_id = current_principal.get()
        if not principal_id:
            return None
        with get_db_session() as session:
            row = session.scalars(select(PrincipalRow).filter_by(principal_id=principal_id)).first()
        return row.tenant_id if row else None

    @staticmethod
    def _tenant_from_subdomain() -> str | None:
        tenant = current_tenant()
        return tenant.id if tenant else None

    @staticmethod
    def _tenant_from_ref(ref: dict[str, Any] | None) -> str | None:
        if not isinstance(ref, dict):
            return None
        account_id = ref.get("account_id")
        if not isinstance(account_id, str) or ":" not in account_id:
            return None
        prefix, _ = account_id.split(":", 1)
        return prefix

    @staticmethod
    def _tenant_exists(tenant_id: str) -> bool:
        with get_db_session() as session:
            row = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        return row is not None and row.is_active


def _auth_info_to_dict(auth_info: AuthInfo | None) -> dict[str, Any] | None:
    if auth_info is None:
        return None
    return {
        "kind": auth_info.kind,
        "key_id": auth_info.key_id,
        "principal": auth_info.principal,
        "scopes": list(auth_info.scopes),
    }
