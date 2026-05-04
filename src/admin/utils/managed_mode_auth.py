"""Managed-mode auth bypass for per-tenant admin UI routes.

When ``MANAGED_INSTANCE=true`` and a tenant is flagged
``managed_externally=True``, requests to ``/tenant/<id>/*`` are
authenticated via the ``X-Identity-*`` headers forwarded by the upstream
proxy (Scope3 Storefront, etc.) — NOT via the salesagent's own Google
OAuth flow. This module owns that decision.

Failure modes match the published contract at
``docs/integration/managed-mode-identity-contract.md``:

- Managed tenant + missing/incomplete headers → 403 ``identity_required``
- Managed tenant + ``X-Identity-Org-Id`` doesn't match the tenant's
  ``external_org_id`` → 403 ``identity_org_mismatch``
- Open-instance tenant (managed_externally=False) on a managed instance
  → falls through to Google OAuth (managed instances still host
  open-instance tenants for legacy / staff use)

The role enum (``admin | member | viewer``) is parsed but not yet
enforced for fine-grained authorization — sprint 4 hardening will scope
nav and hide platform-config pages by role. Today any valid role grants
the same level of access (UI sees the user as authenticated for that
tenant).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from src.admin.middleware.identity_propagation import (
    InvalidPropagatedIdentity,
    PropagatedIdentity,
    read_identity_from_request,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagedAuthOk:
    """Bypass succeeded — caller should populate ``g.user`` and proceed."""

    identity: PropagatedIdentity
    tenant_id: str


@dataclass(frozen=True)
class ManagedAuthDeny:
    """Bypass rejected — caller should return ``(error, message, 403)``."""

    error: Literal["identity_required", "identity_org_mismatch", "identity_role_invalid"]
    message: str


@dataclass(frozen=True)
class ManagedAuthPassthrough:
    """Managed-mode doesn't apply — caller should run normal OAuth checks."""


ManagedAuthResult = ManagedAuthOk | ManagedAuthDeny | ManagedAuthPassthrough


def is_managed_instance() -> bool:
    """``MANAGED_INSTANCE=true`` toggles the contract on globally.

    Re-read on every check so tests can flip the env var without process
    restart. Cheap — single env var lookup.
    """
    return os.environ.get("MANAGED_INSTANCE", "").lower() == "true"


def _load_tenant(tenant_id: str) -> Tenant | None:
    with get_db_session() as session:
        return session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()


def authorize_managed_request(request, tenant_id: str) -> ManagedAuthResult:
    """Resolve managed-mode auth for a per-tenant admin UI request.

    ``request`` must expose Flask's request shape (``.headers`` is the
    only attribute used; the helper is decoupled from Flask).

    Returns:
        - ``ManagedAuthPassthrough`` when ``MANAGED_INSTANCE`` is unset OR
          the tenant isn't managed externally — caller falls through to
          its normal auth path.
        - ``ManagedAuthDeny`` when the tenant IS managed but the headers
          are missing, malformed, or claim a different org. Caller MUST
          return 403 with the named error code.
        - ``ManagedAuthOk`` when the headers authorize this request.
          Caller populates ``g.user`` and proceeds.
    """
    if not is_managed_instance():
        return ManagedAuthPassthrough()

    tenant = _load_tenant(tenant_id)
    if tenant is None:
        # Not our concern — let the route handler 404 / 403 as it sees fit.
        return ManagedAuthPassthrough()

    if not bool(getattr(tenant, "managed_externally", False)):
        # Open-instance tenant living on a managed deployment. Falls
        # through to the salesagent's Google OAuth flow.
        return ManagedAuthPassthrough()

    try:
        identity = read_identity_from_request(request)
    except InvalidPropagatedIdentity as exc:
        return ManagedAuthDeny(error="identity_required", message=str(exc))

    if identity is None:
        return ManagedAuthDeny(
            error="identity_required",
            message=(
                f"Tenant {tenant_id!r} is managed_externally — request must include "
                "X-Identity-Email, X-Identity-Org-Id, X-Identity-Role, X-Identity-Source headers."
            ),
        )

    if identity.org_id != tenant.external_org_id:
        return ManagedAuthDeny(
            error="identity_org_mismatch",
            message=(
                f"X-Identity-Org-Id {identity.org_id!r} does not match tenant {tenant_id!r}'s "
                f"external_org_id {tenant.external_org_id!r}."
            ),
        )

    return ManagedAuthOk(identity=identity, tenant_id=tenant_id)


def synthetic_user_dict(identity: PropagatedIdentity) -> dict[str, object]:
    """Build the ``g.user`` dict that downstream code reads.

    Matches the shape Google OAuth produces (``email`` key) plus
    managed-mode breadcrumbs so audit logs can distinguish the auth
    source. This dict is request-scoped — never written to the Flask
    session cookie, since managed-mode auth is stateless.
    """
    return {
        "email": identity.email,
        "managed_mode": True,
        "org_id": identity.org_id,
        "role": identity.role,
        "source": identity.source,
        "user_id": identity.user_id,
    }
