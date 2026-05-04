"""MediaBuyStore for targeting_overlay echo per spec contract.

Sellers claiming the ``property-lists`` or ``collection-lists`` specialism
must echo persisted ``property_list`` / ``collection_list`` references in
``packages[].targeting_overlay`` on ``get_media_buys`` responses.

The framework's ``create_media_buy_store`` factory wraps an adopter store
with specialism-aware gating — adopters supply the persistence, the framework
gates whether to echo based on declared capabilities.

Skeleton.
"""

from __future__ import annotations


# from adcp.decisioning import MediaBuyStore, create_media_buy_store
# from src.core.database.models import MediaBuy


# class SalesagentMediaBuyStore:
#     """Persist + echo targeting_overlay over the existing media_buys table."""
#
#     async def persist_from_create(self, request, response): ...
#     async def merge_from_update(self, request, response): ...
#     async def backfill(self, response): ...
