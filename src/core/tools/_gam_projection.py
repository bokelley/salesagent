"""Projection of GAM orders/line items into get_media_buys responses.

When an operator assigns a buyer agent to a ``GamAdvertiser`` (via the
buyer-routing UI), that agent's ``get_media_buys`` response should
include the advertiser's GAM orders alongside any native AdCP buys.

This module performs that projection in-memory at read time. Nothing is
written to ``media_buys`` until the buyer first calls ``update_media_buy``
on a projected ID — at that point a real row is materialized so packages,
push configs, and audit logs have somewhere to attach. See
``update_media_buy`` for the materialization path.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from adcp.types import MediaBuyStatus
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import GAMLineItem, GAMOrder, MediaBuy
from src.core.database.repositories.gam_sync import GAMSyncRepository

# Order-level status. Date logic decides between pending_start / active /
# completed for non-terminal states; PAUSED / CANCELED / DELETED short-
# circuit the date check.
_ORDER_TERMINAL_STATUS: dict[str, MediaBuyStatus] = {
    "PAUSED": MediaBuyStatus.paused,
    "CANCELED": MediaBuyStatus.canceled,
    "DELETED": MediaBuyStatus.canceled,
}


def project_gam_status(order_status: str | None, start: date | None, end: date | None, today: date) -> MediaBuyStatus:
    """Map a GAM order's status + flight dates to an AdCP MediaBuyStatus."""
    if order_status and order_status.upper() in _ORDER_TERMINAL_STATUS:
        return _ORDER_TERMINAL_STATUS[order_status.upper()]
    if start is not None and today < start:
        return MediaBuyStatus.pending_start
    if end is not None and today > end:
        return MediaBuyStatus.completed
    return MediaBuyStatus.active


def projected_media_buy_id(order_id: str) -> str:
    """Stable, deterministic ID for a projected GAM order."""
    return f"gam_{order_id}"


def projected_package_id(line_item_id: str) -> str:
    """Stable, deterministic ID for a projected GAM line item."""
    return f"gam_li_{line_item_id}"


def fetch_materialized_external_ids(session: Session, tenant_id: str, order_ids: Iterable[str]) -> set[str]:
    """Return the GAM order ids that already have a materialized MediaBuy row.

    Materialized buys take precedence — we don't double-count them in the
    projection. Identified by ``source = 'gam_import'`` and ``external_id``
    matching the order id.
    """
    ids = list(order_ids)
    if not ids:
        return set()
    rows = session.scalars(
        select(MediaBuy.external_id).where(
            MediaBuy.tenant_id == tenant_id,
            MediaBuy.source == "gam_import",
            MediaBuy.external_id.in_(ids),
        )
    ).all()
    return {r for r in rows if r is not None}


def project_orders_for_principal(
    session: Session,
    tenant_id: str,
    principal_id: str,
    media_buy_ids_filter: list[str] | None = None,
) -> tuple[list[GAMOrder], dict[str, list[GAMLineItem]]]:
    """Fetch GAM orders + line items the principal should see via projection.

    Returns the orders and a map of order_id -> [line items]. Filters out
    orders whose advertiser is not assigned to this principal. If
    ``media_buy_ids_filter`` is provided, only includes orders whose
    projected media_buy_id is in the filter.
    """
    repo = GAMSyncRepository(session, tenant_id)
    advertiser_ids = repo.list_advertiser_ids_assigned_to(principal_id)
    if not advertiser_ids:
        return [], {}

    orders = repo.list_orders_for_advertisers(advertiser_ids)

    if media_buy_ids_filter is not None:
        wanted_order_ids = {
            mid[len("gam_") :]
            for mid in media_buy_ids_filter
            if mid.startswith("gam_") and not mid.startswith("gam_li_")
        }
        orders = [o for o in orders if o.order_id in wanted_order_ids]

    if not orders:
        return [], {}

    materialized = fetch_materialized_external_ids(session, tenant_id, [o.order_id for o in orders])
    orders = [o for o in orders if o.order_id not in materialized]

    if not orders:
        return [], {}

    line_items = repo.list_line_items_for_orders([o.order_id for o in orders])
    line_items_by_order: dict[str, list[GAMLineItem]] = {}
    for li in line_items:
        line_items_by_order.setdefault(li.order_id, []).append(li)

    return orders, line_items_by_order


def order_to_media_buy_fields(order: GAMOrder) -> dict:
    """Extract _MediaBuyData-shaped fields from a GAMOrder.

    Keeping this as a dict (not building _MediaBuyData here) avoids a
    circular import with ``media_buy_list``.
    """
    start_date_val: date | None = order.start_date.date() if isinstance(order.start_date, datetime) else None
    end_date_val: date | None = order.end_date.date() if isinstance(order.end_date, datetime) else None

    return {
        "media_buy_id": projected_media_buy_id(order.order_id),
        "currency": order.currency_code,
        "budget": Decimal(str(order.total_budget)) if order.total_budget is not None else None,
        "start_date": start_date_val,
        "end_date": end_date_val,
        "start_time": order.start_date if isinstance(order.start_date, datetime) else None,
        "end_time": order.end_date if isinstance(order.end_date, datetime) else None,
        "raw_request": {"gam_order_id": order.order_id, "imported": True},
        "created_at": order.created_at,
        "updated_at": order.last_modified_date or order.updated_at,
    }


def line_item_to_package_fields(line_item: GAMLineItem) -> dict:
    """Extract _PackageData-shaped fields from a GAMLineItem."""
    media_buy_id = projected_media_buy_id(line_item.order_id)
    package_id = projected_package_id(line_item.line_item_id)
    bid_price = Decimal(str(line_item.cost_per_unit)) if line_item.cost_per_unit is not None else None

    package_config: dict = {
        "platform_line_item_id": line_item.line_item_id,
        "imported": True,
        "gam_line_item_status": line_item.status,
    }

    return {
        "media_buy_id": media_buy_id,
        "package_id": package_id,
        "package_config": package_config,
        "budget": None,
        "bid_price": bid_price,
    }
