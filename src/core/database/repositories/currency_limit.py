"""Currency limit repository — tenant-scoped access to CurrencyLimit models.

Provides typed methods for querying currency limits so _impl functions
do not need raw select() calls or direct model imports for CurrencyLimit.

beads: salesagent-qo8a (repository pattern enforcement)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import CurrencyLimit


class CurrencyLimitRepository:
    """Tenant-scoped read access for currency limits.

    All queries filter by tenant_id automatically.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_for_currency(self, currency_code: str) -> CurrencyLimit | None:
        """Get the CurrencyLimit for a specific currency, or None if not configured."""
        stmt = select(CurrencyLimit).filter_by(
            tenant_id=self._tenant_id,
            currency_code=currency_code,
        )
        return self._session.scalars(stmt).first()

    def list_all(self) -> list[CurrencyLimit]:
        """Return every CurrencyLimit row for this tenant.

        Used by settings pages that render the full per-currency table
        (budget controls). Order is whatever the DB returns — callers
        sort if they need stability.
        """
        stmt = select(CurrencyLimit).filter_by(tenant_id=self._tenant_id)
        return list(self._session.scalars(stmt).all())
