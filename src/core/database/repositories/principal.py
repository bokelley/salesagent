"""Principal repository — tenant-scoped data access for principals.

Supports lookup by principal_id, access_token, and storefront-supplied
external_id (for idempotent creation via /api/v1/principals).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Principal


class PrincipalRepository:
    """Tenant-scoped data access for Principal."""

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "principal_id", "created_at"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_by_id(self, principal_id: str) -> Principal | None:
        return self._session.scalars(
            select(Principal).where(
                Principal.tenant_id == self._tenant_id,
                Principal.principal_id == principal_id,
            )
        ).first()

    def get_by_external_id(self, external_id: str) -> Principal | None:
        """Idempotency lookup. Returns None if no principal claims this external_id."""
        return self._session.scalars(
            select(Principal).where(
                Principal.tenant_id == self._tenant_id,
                Principal.external_id == external_id,
            )
        ).first()

    def get_by_access_token(self, access_token: str) -> Principal | None:
        """Cross-tenant lookup by token. Tenant guard enforced via the token's
        scope rather than this repository — used by the boundary layer."""
        return self._session.scalars(select(Principal).where(Principal.access_token == access_token)).first()

    def list_all(self) -> list[Principal]:
        return list(
            self._session.scalars(
                select(Principal).where(Principal.tenant_id == self._tenant_id).order_by(Principal.principal_id)
            ).all()
        )

    def add(self, principal: Principal) -> None:
        if principal.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: principal.tenant_id={principal.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(principal)

    def delete(self, principal: Principal) -> None:
        if principal.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: principal.tenant_id={principal.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.delete(principal)
