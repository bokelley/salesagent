"""AccountStore over the salesagent ``principals`` and ``tenants`` tables.

Resolution order:
1. Subdomain-set contextvar (set by :class:`SubdomainTenantMiddleware`) — production.
2. Explicit ``account.account_id`` like ``"tenant-a:acct_demo"`` — storyboards/dev.
3. Reject with ``ACCOUNT_NOT_FOUND``.

The resolved Account's ``metadata['tenant_id']`` is what
:class:`PlatformRouter` reads to pick the per-tenant ``DecisioningPlatform``.
"""

from __future__ import annotations

from typing import Any, Literal

from adcp.decisioning import AdcpError
from adcp.decisioning.context import AuthInfo
from adcp.decisioning.types import Account
from adcp.server import current_tenant
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant


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
        tenant_id = self._tenant_from_subdomain() or self._tenant_from_ref(ref)
        if tenant_id is None or not self._tenant_exists(tenant_id):
            raise AdcpError(
                "ACCOUNT_NOT_FOUND",
                message=(
                    "Could not resolve a tenant. Send via the tenant "
                    "subdomain (e.g. acme.example.com) or pass "
                    "account.account_id with a 'tenant_id:' prefix."
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
