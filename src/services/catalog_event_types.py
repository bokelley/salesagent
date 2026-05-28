"""Shared catalog webhook event taxonomy."""

from __future__ import annotations

PRODUCT_PROTOCOL_CATALOG_EVENT_TYPES: tuple[str, ...] = (
    "product.created",
    "product.updated",
    "product.priced",
    "product.removed",
)

SIGNAL_PROTOCOL_CATALOG_EVENT_TYPES: tuple[str, ...] = (
    "signal.created",
    "signal.updated",
    "signal.priced",
    "signal.removed",
)

WHOLESALE_FEED_EVENT_TYPES: tuple[str, ...] = ("wholesale_feed.bulk_change",)

CATALOG_CHANGE_EVENT_TYPES: tuple[str, ...] = (
    *PRODUCT_PROTOCOL_CATALOG_EVENT_TYPES,
    *SIGNAL_PROTOCOL_CATALOG_EVENT_TYPES,
    *WHOLESALE_FEED_EVENT_TYPES,
)

TENANT_MANAGEMENT_CATALOG_EVENT_TYPES: tuple[str, ...] = (
    "product.created",
    "product.updated",
    "product.priced",
    "product.removed",
    "signal.created",
    "signal.updated",
    "signal.priced",
    "signal.removed",
    *WHOLESALE_FEED_EVENT_TYPES,
)

ACCOUNT_NOTIFICATION_EVENT_TYPES = frozenset(
    (
        "creative.status_changed",
        "creative.purged",
        "product.created",
        "product.updated",
        "product.removed",
        "signal.created",
        "signal.updated",
        "signal.removed",
    )
)


def catalog_event_action(action: str) -> str:
    """Normalize local CRUD verbs to catalog event suffixes."""
    return "removed" if action in {"deleted", "removed"} else action
