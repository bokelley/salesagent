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

This store also implements the framework's optional
:class:`AccountStoreUpsert` / :class:`AccountStoreList` Protocols so
``sync_accounts`` / ``list_accounts`` work on the wire — the framework's
stub :class:`PlatformHandler` dispatchers are rebound onto these methods
by :mod:`core.platforms.account_polyfill`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from adcp.decisioning import AdcpError
from adcp.decisioning.context import AuthInfo
from adcp.decisioning.types import Account
from adcp.server import current_tenant
from adcp.server.auth import current_principal
from adcp.server.auth import current_tenant as auth_current_tenant
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow
from src.core.database.models import Tenant
from src.core.exceptions import AdCPError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import (
    ListAccountsRequest,
    SyncAccountsRequest,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.accounts import _list_accounts_impl, _sync_accounts_impl

logger = logging.getLogger(__name__)


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
        tenant_id = self._tenant_from_principal() or self._tenant_from_subdomain() or self._tenant_from_ref(ref)
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

    # ----- AccountStoreUpsert / AccountStoreList Protocols -----------
    #
    # Account dispatch on the wire flows through the AccountStore's
    # ``upsert`` / ``list`` methods (see ``adcp.decisioning.accounts``).
    # ``core.platforms.account_polyfill`` wires the framework's
    # ``PlatformHandler.sync_accounts`` / ``list_accounts`` stubs onto
    # these methods so the wire skill calls actually reach our impl.

    # The argument shape changes once we bump to the adcp release that
    # carries adcontextprotocol/adcp-client-python#610 — the framework
    # will call ``upsert(refs=list[AccountReference], ctx=...)`` and
    # ``list(filter=dict|None, ctx=...)`` instead of passing the full
    # parsed request. Both branches accept either shape so the bump is
    # a one-line ``adcp >= X.Y`` requirement bump rather than a code
    # rewrite. The legacy SyncAccountsRequest / ListAccountsRequest
    # branches also keep ``core.platforms.account_polyfill`` working in
    # the meantime — that polyfill forwards the parsed request as
    # ``params``.

    async def upsert(
        self,
        payload: Any,
        ctx: Any | None = None,
    ) -> Any:
        """Forward ``sync_accounts`` to ``_sync_accounts_impl``.

        Accepts either:
        * a ``SyncAccountsRequest`` / dict (today's polyfill path), or
        * a ``list[AccountReference]`` (the framework's contract once
          adcp-client-python#610 lands).
        """
        req = self._coerce_sync_accounts_payload(payload)
        identity = self._identity_from_ctx(ctx)
        try:
            return await _sync_accounts_impl(req=req, identity=identity)
        except AdCPError as exc:
            raise self._translate(exc) from exc

    async def list(
        self,
        payload: Any = None,
        ctx: Any | None = None,
    ) -> Any:
        """Forward ``list_accounts`` to ``_list_accounts_impl``.

        Accepts either:
        * a ``ListAccountsRequest`` / dict carrying the full request
          (today's polyfill path), or
        * a flat filter dict ``{status, sandbox, pagination}`` (the
          framework's contract once adcp-client-python#610 lands), or
        * ``None`` for the no-filter case.
        """
        req = self._coerce_list_accounts_payload(payload)
        identity = self._identity_from_ctx(ctx)
        try:
            return await asyncio.to_thread(_list_accounts_impl, req, identity)
        except AdCPError as exc:
            raise self._translate(exc) from exc

    @staticmethod
    def _coerce_sync_accounts_payload(payload: Any) -> SyncAccountsRequest:
        """Normalise the framework's ``upsert`` argument into a
        :class:`SyncAccountsRequest` for the impl. ``list`` is the
        post-#610 ``refs`` shape; everything else is treated as the
        full request."""
        if isinstance(payload, SyncAccountsRequest):
            return payload
        if isinstance(payload, list):
            # Framework projected ``params.accounts`` to a list of refs;
            # rebuild the request with a synthesised idempotency_key.
            return SyncAccountsRequest.model_construct(
                accounts=payload,
                idempotency_key=f"polyfill-{id(payload):x}",
            )
        if hasattr(payload, "model_dump"):
            return SyncAccountsRequest(**payload.model_dump(exclude_none=True))
        if isinstance(payload, dict):
            return SyncAccountsRequest(**payload)
        return SyncAccountsRequest.model_validate(payload)

    @staticmethod
    def _coerce_list_accounts_payload(payload: Any) -> ListAccountsRequest | None:
        """Normalise the framework's ``list`` argument into a
        :class:`ListAccountsRequest`. ``None`` and an empty filter dict
        both map to ``None`` (the impl's no-filter path)."""
        if payload is None:
            return None
        if isinstance(payload, ListAccountsRequest):
            return payload
        if hasattr(payload, "model_dump"):
            return ListAccountsRequest(**payload.model_dump(exclude_none=True))
        if isinstance(payload, dict):
            if not payload:
                return None
            return ListAccountsRequest(**payload)
        return ListAccountsRequest.model_validate(payload)

    def _identity_from_ctx(self, ctx: Any | None) -> ResolvedIdentity:
        """Build a :class:`ResolvedIdentity` from the framework
        :class:`ResolveContext` (or fall back to the request-scope
        ContextVars when called outside the dispatch shim)."""
        from src.core.config_loader import get_tenant_by_id

        principal_id = current_principal.get()
        tenant_id = auth_current_tenant.get()
        if not tenant_id and ctx is not None:
            agent = getattr(ctx, "agent", None)
            tenant_id = getattr(agent, "tenant_id", None)
        if not tenant_id:
            raise AdcpError(
                "ACCOUNT_NOT_FOUND",
                message=(
                    "sync_accounts/list_accounts requires an authenticated "
                    "principal — no tenant resolved on the request context."
                ),
                recovery="terminal",
                field="account",
            )
        tenant_dict = get_tenant_by_id(tenant_id)
        return ResolvedIdentity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            tenant=tenant_dict,
            protocol="mcp",
            testing_context=AdCPTestContext(),
        )

    @staticmethod
    def _translate(exc: AdCPError) -> AdcpError:
        return AdcpError(
            exc.error_code,
            message=exc.message or str(exc),
            recovery=exc.recovery,
            details=exc.details if isinstance(exc.details, dict) else None,
        )


def _auth_info_to_dict(auth_info: AuthInfo | None) -> dict[str, Any] | None:
    if auth_info is None:
        return None
    return {
        "kind": auth_info.kind,
        "key_id": auth_info.key_id,
        "principal": auth_info.principal,
        "scopes": list(auth_info.scopes),
    }
