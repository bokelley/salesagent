"""AdvertiserMapping repository — tenant-scoped data access for
``AdvertiserRoutingRule`` rows.

External vocabulary at the REST boundary is ``advertiser-mappings``; the
underlying storage is ``advertiser_routing_rules`` (the impl is a
precedence-ordered routing chain). Same one-line API/internal split as the
existing Tenant Management API.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AdvertiserRoutingRule, GamAdvertiser


class AdvertiserMappingRepository:
    """Tenant-scoped data access for ``AdvertiserRoutingRule`` rows.

    Storefront-facing field renames happen at the API layer; this repo
    deals in the underlying column names.
    """

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "tenant_id", "created_at"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_by_id(self, mapping_id: str) -> AdvertiserRoutingRule | None:
        return self._session.scalars(
            select(AdvertiserRoutingRule).where(
                AdvertiserRoutingRule.tenant_id == self._tenant_id,
                AdvertiserRoutingRule.id == mapping_id,
            )
        ).first()

    def list_all(self) -> list[AdvertiserRoutingRule]:
        return list(
            self._session.scalars(
                select(AdvertiserRoutingRule)
                .where(AdvertiserRoutingRule.tenant_id == self._tenant_id)
                .order_by(AdvertiserRoutingRule.created_at)
            ).all()
        )

    def find_by_natural_key(
        self,
        *,
        principal_id: str | None,
        operator_domain: str,
        brand_house: str | None,
        brand_id: str | None,
    ) -> AdvertiserRoutingRule | None:
        """Exact-match lookup on the natural key (with NULL treated literally,
        not as wildcard — wildcards are a runtime resolution behavior, not a
        uniqueness behavior)."""
        stmt = select(AdvertiserRoutingRule).where(
            AdvertiserRoutingRule.tenant_id == self._tenant_id,
            AdvertiserRoutingRule.operator_domain == operator_domain,
        )
        stmt = stmt.where(
            AdvertiserRoutingRule.principal_id.is_(None)
            if principal_id is None
            else AdvertiserRoutingRule.principal_id == principal_id
        )
        stmt = stmt.where(
            AdvertiserRoutingRule.brand_house.is_(None)
            if brand_house is None
            else AdvertiserRoutingRule.brand_house == brand_house
        )
        stmt = stmt.where(
            AdvertiserRoutingRule.brand_id.is_(None) if brand_id is None else AdvertiserRoutingRule.brand_id == brand_id
        )
        return self._session.scalars(stmt).first()

    def add(self, rule: AdvertiserRoutingRule) -> None:
        if rule.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: rule.tenant_id={rule.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(rule)

    def delete(self, rule: AdvertiserRoutingRule) -> None:
        if rule.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: rule.tenant_id={rule.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.delete(rule)


class GamAdvertiserRepository:
    """Read-only access to the synced adapter-advertiser cache."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def list_all(self, include_inactive: bool = False) -> list[GamAdvertiser]:
        stmt = select(GamAdvertiser).where(GamAdvertiser.tenant_id == self._tenant_id)
        if not include_inactive:
            stmt = stmt.where(GamAdvertiser.status == "active")
        return list(self._session.scalars(stmt.order_by(GamAdvertiser.name)).all())
