"""Embedded-mode auth bypass for per-tenant admin UI routes.

When ``MANAGED_INSTANCE=true`` and a tenant is flagged
``is_embedded=True``, requests to ``/tenant/<id>/*`` are
authenticated via the ``X-Identity-*`` headers forwarded by the upstream
proxy (Scope3 Storefront, etc.) — NOT via the salesagent's own Google
OAuth flow. This module owns that decision.

Failure modes match the published contract at
``docs/integration/managed-mode-identity-contract.md``:

- Embedded tenant + missing/incomplete headers → 403 ``identity_required``
- Embedded tenant + ``X-Identity-Org-Id`` doesn't match the tenant's
  ``external_org_id`` → 403 ``identity_org_mismatch``
- Open-instance tenant (is_embedded=False) on a managed instance
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
class EmbeddedAuthOk:
    """Bypass succeeded — caller should populate ``g.user`` and proceed."""

    identity: PropagatedIdentity
    tenant_id: str


@dataclass(frozen=True)
class EmbeddedAuthDeny:
    """Bypass rejected — caller should return ``(error, message, 403)``."""

    error: Literal["identity_required", "identity_org_mismatch", "identity_role_invalid"]
    message: str


@dataclass(frozen=True)
class EmbeddedAuthPassthrough:
    """Embedded-mode doesn't apply — caller should run normal OAuth checks."""


EmbeddedAuthResult = EmbeddedAuthOk | EmbeddedAuthDeny | EmbeddedAuthPassthrough


def is_managed_instance() -> bool:
    """``MANAGED_INSTANCE=true`` toggles the contract on globally.

    Re-read on every check so tests can flip the env var without process
    restart. Cheap — single env var lookup.
    """
    return os.environ.get("MANAGED_INSTANCE", "").lower() == "true"


def _load_tenant(tenant_id: str) -> Tenant | None:
    with get_db_session() as session:
        return session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()


def authorize_embedded_request(request, tenant_id: str) -> EmbeddedAuthResult:
    """Resolve embedded-mode auth for a per-tenant admin UI request.

    ``request`` must expose Flask's request shape (``.headers`` is the
    only attribute used; the helper is decoupled from Flask).

    Returns:
        - ``EmbeddedAuthPassthrough`` when ``MANAGED_INSTANCE`` is unset OR
          the tenant isn't embedded — caller falls through to
          its normal auth path.
        - ``EmbeddedAuthDeny`` when the tenant IS embedded but the headers
          are missing, malformed, or claim a different org. Caller MUST
          return 403 with the named error code.
        - ``EmbeddedAuthOk`` when the headers authorize this request.
          Caller populates ``g.user`` and proceeds.
    """
    if not is_managed_instance():
        return EmbeddedAuthPassthrough()

    tenant = _load_tenant(tenant_id)
    if tenant is None:
        # Not our concern — let the route handler 404 / 403 as it sees fit.
        return EmbeddedAuthPassthrough()

    if not bool(getattr(tenant, "is_embedded", False)):
        # Open-instance tenant living on a managed deployment. Falls
        # through to the salesagent's Google OAuth flow.
        return EmbeddedAuthPassthrough()

    try:
        identity = read_identity_from_request(request)
    except InvalidPropagatedIdentity as exc:
        return EmbeddedAuthDeny(error="identity_required", message=str(exc))

    if identity is None:
        return EmbeddedAuthDeny(
            error="identity_required",
            message=(
                f"Tenant {tenant_id!r} is is_embedded — request must include "
                "X-Identity-Email, X-Identity-Org-Id, X-Identity-Role, X-Identity-Source headers."
            ),
        )

    if identity.org_id != tenant.external_org_id:
        return EmbeddedAuthDeny(
            error="identity_org_mismatch",
            message=(
                f"X-Identity-Org-Id {identity.org_id!r} does not match tenant {tenant_id!r}'s "
                f"external_org_id {tenant.external_org_id!r}."
            ),
        )

    return EmbeddedAuthOk(identity=identity, tenant_id=tenant_id)


def synthetic_user_dict(identity: PropagatedIdentity) -> dict[str, object]:
    """Build the ``g.user`` dict that downstream code reads.

    Matches the shape Google OAuth produces (``email`` key) plus
    embedded-mode breadcrumbs so audit logs can distinguish the auth
    source. This dict is request-scoped — never written to the Flask
    session cookie, since embedded-mode auth is stateless.
    """
    return {
        "email": identity.email,
        "embedded_mode": True,
        "org_id": identity.org_id,
        "role": identity.role,
        "source": identity.source,
        "user_id": identity.user_id,
    }
