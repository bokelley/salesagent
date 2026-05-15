"""Read-only client for SpringServe supply-side entities.

Endpoint reference:
- GET /api/v0/supply_partners
- GET /api/v0/supply_partners/{id}
- GET /api/v0/supply_tags
- GET /api/v0/supply_tags/{id}

Supply Partners are the account-level grouping (one per business unit
in an O&O publisher's account, or one per external publisher in an
SSP-style deployment). Supply Tags are the actual inventory units
underneath -- one per app/player/stream/podcast feed.

Today these endpoints return 403 on the operator's test account. The
sync still walks the surface for forward compatibility and surfaces a
clean ``SupplyScopeNotGranted`` when SpringServe denies the read.
"""

from __future__ import annotations

from typing import Any

from src.adapters.springserve._transport import SpringServeTransport


class SpringServeSupplyClient:
    """Read-only supply-side client bound to one :class:`SpringServeTransport`."""

    def __init__(self, transport: SpringServeTransport):
        self._transport = transport

    def list_supply_partners(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        body = self._transport.get_json("/supply_partners", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []

    def list_supply_tags(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        body = self._transport.get_json("/supply_tags", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []
