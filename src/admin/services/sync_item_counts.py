"""Shared SyncJob progress item-count extraction."""

from __future__ import annotations

from typing import Any


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

    raw_counts = progress.get("counts")
    counts = raw_counts if isinstance(raw_counts, dict) else {}
    for key in ("products_updated", "signals_updated"):
        if key in counts and counts[key] is not None:
            return int(counts[key])
    return None
