"""Compute the recent-buyers rollup shared by the tenant-management API
endpoint and the buyer-routing UI page.

Source data: ``Account`` rows joined to ``MediaBuy`` for activity counts.
Each Account already carries its (operator, brand) natural key + the
resolved ``platform_mappings.google_ad_manager.advertiser_id`` +
``resolved_via`` (Sprint 1.8 stamp).

Returning the raw ORM rows + extracted fields keeps the service
transport-agnostic — the API endpoint maps to its Pydantic response
shape, the buyer-routing page hands the rows directly to Jinja.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, MediaBuy

_ACCOUNT_GAM_KEY = "google_ad_manager"


@dataclass(frozen=True)
class RecentBuyerRow:
    """One distinct (operator, brand_house, brand_id) triple seen recently.

    Purely a transport-shaped DTO — neither the API endpoint nor the
    template needs the underlying ORM Account once we've extracted the
    fields they care about.
    """

    operator_domain: str
    brand_house: str | None
    brand_id: str | None
    last_seen_at: datetime
    request_count: int
    resolved_gam_advertiser_id: str | None
    resolved_via: str
    # Sandbox flag exposed so the page can route rows into the §4
    # Sandbox section instead of the main Activity table.
    sandbox: bool


def _account_advertiser_id(account: Account) -> str | None:
    mappings = account.platform_mappings or {}
    return (mappings.get(_ACCOUNT_GAM_KEY) or {}).get("advertiser_id")


def _coerce_brand(account: Account) -> tuple[str | None, str | None]:
    if account.brand is None:
        return None, None
    if isinstance(account.brand, dict):
        brand_dict: dict = account.brand
    elif hasattr(account.brand, "model_dump"):
        brand_dict = account.brand.model_dump(exclude_none=True)
    else:
        brand_dict = dict(account.brand)
    return brand_dict.get("domain"), brand_dict.get("brand_id")


def compute_recent_buyers(tenant_id: str, *, days: int = 30, limit: int = 100) -> list[RecentBuyerRow]:
    """Return distinct (operator, brand_house, brand_id) triples seen in
    the last ``days`` days, ordered by most-recent activity first.

    Caller is responsible for clamping ``days`` and ``limit`` to whatever
    bounds the surface enforces — the service applies them as-is.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)

    with get_db_session() as session:
        # Aggregate MediaBuy counts + last_seen per Account, scoped to the
        # last N days. Accounts with no recent buys are still returned —
        # the publisher might want to see a buyer agent that's been
        # provisioned but hasn't transacted yet.
        request_count_subq = (
            select(
                MediaBuy.account_id.label("account_id"),
                func.count().label("request_count"),
                func.max(MediaBuy.created_at).label("last_seen_at"),
            )
            .where(
                MediaBuy.tenant_id == tenant_id,
                MediaBuy.created_at >= cutoff,
                MediaBuy.account_id.is_not(None),
            )
            .group_by(MediaBuy.account_id)
            .subquery()
        )

        rows = session.execute(
            select(
                Account,
                request_count_subq.c.request_count,
                request_count_subq.c.last_seen_at,
            )
            .outerjoin(
                request_count_subq,
                Account.account_id == request_count_subq.c.account_id,
            )
            .where(Account.tenant_id == tenant_id)
            .order_by(
                request_count_subq.c.last_seen_at.desc().nullslast(),
                Account.created_at.desc(),
            )
            .limit(limit)
        ).all()

        result: list[RecentBuyerRow] = []
        for account, request_count, last_seen in rows:
            brand_house, brand_id = _coerce_brand(account)
            result.append(
                RecentBuyerRow(
                    operator_domain=account.operator or "",
                    brand_house=brand_house,
                    brand_id=brand_id,
                    last_seen_at=last_seen or account.created_at,
                    request_count=int(request_count or 0),
                    resolved_gam_advertiser_id=_account_advertiser_id(account),
                    resolved_via=account.resolved_via or "unknown",
                    sandbox=bool(account.sandbox),
                )
            )
        return result
