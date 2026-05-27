"""Shared webhook event taxonomies."""

from __future__ import annotations

CATALOG_CHANGE_EVENT_TYPES: tuple[str, ...] = (
    "product.created",
    "product.updated",
    "product.priced",
    "product.removed",
    "signal.created",
    "signal.updated",
    "signal.priced",
    "signal.removed",
    "wholesale_feed.bulk_change",
)

ACCOUNT_NOTIFICATION_CATALOG_EVENT_TYPES: tuple[str, ...] = (
    "product.created",
    "product.updated",
    "product.removed",
    "signal.created",
    "signal.updated",
    "signal.removed",
)

ACCOUNT_NOTIFICATION_EVENT_TYPES = frozenset(
    (
        "creative.status_changed",
        "creative.purged",
        *ACCOUNT_NOTIFICATION_CATALOG_EVENT_TYPES,
    )
)
