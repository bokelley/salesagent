"""Admin compatibility import for SyncJob progress item-count extraction."""

from __future__ import annotations

from src.services.sync_progress import sync_item_count_from_progress

__all__ = ["sync_item_count_from_progress"]
