"""SignalUsage repository — count active media buys referencing a TenantSignal.

A ``signal_id`` lands inside the create_media_buy request JSON at
``packages[*].targeting_overlay.audience_include`` and
``audience_exclude``. We persist that payload verbatim on
``MediaBuy.raw_request`` (JSONB). To answer "which signals are buyers
actually referencing right now?" we scan the active subset of buys for
the tenant and walk the JSON in Python.

Active = status in ('active', 'approved'). Paused buys still count —
the references are still live, the buy is just temporarily not serving.
Completed and cancelled buys are excluded; the references are
historical and shouldn't block a delete.

Why Python iteration and not JSONB path queries: the rest of the
codebase walks ``raw_request`` in Python (see
``src/core/tools/media_buy_delivery.py``) and operates at publisher
scale — typically <1000 active buys per tenant. A single SELECT + dict
walk is cheaper than maintaining a JSONB-path query idiom that doesn't
exist elsewhere in the codebase.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import MediaBuy


@dataclass(frozen=True)
class SignalUsage:
    """Per-signal usage snapshot over a tenant's active media buys."""

    active_buy_count: int
    last_referenced_at: datetime | None


_ACTIVE_STATUSES: tuple[str, ...] = ("active", "approved")


def _iter_referenced_signal_ids(raw_request: dict[str, Any] | None) -> Iterable[str]:
    """Yield every ``signal_id`` referenced by a create_media_buy payload.

    Handles missing keys defensively — older payloads may not have a
    ``packages`` field, or may omit ``targeting_overlay`` per package.
    Yields duplicates: the same signal can appear in multiple packages
    of one buy. Callers deduplicate per buy.
    """
    if not raw_request:
        return
    for pkg in raw_request.get("packages") or []:
        overlay = (pkg or {}).get("targeting_overlay") or {}
        for field in ("audience_include", "audience_exclude"):
            for sid in overlay.get(field) or []:
                if isinstance(sid, str) and sid:
                    yield sid


class SignalUsageRepository:
    """Tenant-scoped scan of media buys to count signal references."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def usage_index(self) -> dict[str, SignalUsage]:
        """Return ``signal_id -> SignalUsage`` for every signal referenced
        by an active buy in this tenant.

        Signals that no active buy references are absent from the dict —
        callers treat missing as zero references. Last-referenced is the
        max ``MediaBuy.updated_at`` over buys that reference the signal.
        """
        stmt = select(MediaBuy.raw_request, MediaBuy.updated_at).where(
            MediaBuy.tenant_id == self._tenant_id,
            MediaBuy.status.in_(_ACTIVE_STATUSES),
        )
        counts: dict[str, int] = {}
        last_seen: dict[str, datetime] = {}
        for raw_request, updated_at in self._session.execute(stmt).all():
            seen_in_buy: set[str] = set()
            for sid in _iter_referenced_signal_ids(raw_request):
                if sid in seen_in_buy:
                    continue
                seen_in_buy.add(sid)
                counts[sid] = counts.get(sid, 0) + 1
                prior = last_seen.get(sid)
                if updated_at is not None and (prior is None or updated_at > prior):
                    last_seen[sid] = updated_at
        return {sid: SignalUsage(active_buy_count=counts[sid], last_referenced_at=last_seen.get(sid)) for sid in counts}

    def count_references(self, signal_id: str) -> int:
        """Count active media buys referencing ``signal_id``.

        Convenience wrapper around :meth:`usage_index` — most callers
        already need the full index (it powers both the inline chips and
        the delete confirmation), but a one-off lookup is occasionally
        cheaper to read at the call site.
        """
        if not signal_id:
            return 0
        return self.usage_index().get(signal_id, SignalUsage(0, None)).active_buy_count
