"""Shared SyncJob progress helpers.

Sync producers should write a top-level ``item_count`` on terminal progress.
Readers can still infer counts for older rows, but new writers should not make
every consumer rediscover sync-specific nested count keys.
"""

from __future__ import annotations

from typing import Any

_TERMINAL_ITEM_COUNT_KEYS = (
    "products_updated",
    "signals_updated",
    "advertisers_seen",
)


def infer_item_count_from_counts(
    counts: Any,
    *,
    include_items_processed: bool = False,
) -> int | None:
    """Infer a single item count from a ``progress['counts']`` block."""
    if not isinstance(counts, dict):
        return None
    keys: tuple[str, ...] = _TERMINAL_ITEM_COUNT_KEYS
    if include_items_processed:
        keys = (*keys, "items_processed")
    for key in keys:
        if key in counts and counts[key] is not None:
            return int(counts[key])
    if counts:
        sum_counts = {
            key: value
            for key, value in counts.items()
            if value is not None and (include_items_processed or key != "items_processed")
        }
        if sum_counts:
            return sum(int(value) for value in sum_counts.values())
    return None


def sync_item_count_from_progress(
    progress: Any,
    *,
    include_items_processed: bool = False,
) -> int | None:
    """Return the first present item-count field from SyncJob progress.

    ``0`` is a meaningful count, so presence checks must not use truthiness.
    """
    if not isinstance(progress, dict):
        return None

    progress_keys = ["item_count"]
    if include_items_processed:
        progress_keys.append("items_processed")
    for key in progress_keys:
        if key in progress and progress[key] is not None:
            return int(progress[key])

    return infer_item_count_from_counts(
        progress.get("counts"),
        include_items_processed=include_items_processed,
    )


def build_sync_progress(
    *,
    counts: dict[str, int] | None = None,
    errors: dict[str, str] | None = None,
    item_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a terminal SyncJob progress block with normalized count fields."""
    normalized_counts = dict(counts or {})
    progress: dict[str, Any] = {
        "item_count": item_count if item_count is not None else infer_item_count_from_counts(normalized_counts),
        "counts": normalized_counts,
        "errors": dict(errors or {}),
    }
    if metadata is not None:
        progress["metadata"] = dict(metadata)
    progress.update(extra)
    return progress
