"""Read-only client for SpringServe supply-side entities + KV catalog.

Endpoint reference:

Supply hierarchy:
- GET /api/v0/supply_partners                 (top-level seller relationships)
- GET /api/v0/supply_partners/{id}
- GET /api/v0/supply_routers                  (routing groups under a partner)
- GET /api/v0/supply_routers/{id}
- GET /api/v0/supply_tags                     (inventory atoms)
- GET /api/v0/supply_tags?supply_router_id=X  (only the path linking tags to routers --
                                              neither side carries the back-reference)

KV / audience catalog:
- GET /api/v0/keys                            (KV namespaces declared by the publisher)
- GET /api/v0/keys/{id}
- GET /api/v0/value_lists                     (named value sets attached to a key --
                                              this is where publisher-curated audience
                                              segments live, e.g. "Audio M25-54")
- GET /api/v0/value_lists/{id}

Hierarchy invariants (verified live, May 2026):

* A supply_tag belongs to at most one supply_router. There is no
  ``supply_router_id`` field on the tag and no ``supply_tag_ids`` on
  the router -- the relationship is only discoverable via the
  ``?supply_router_id=`` filter on the tag list endpoint.
* Tags may be orphans (belong to no router). These still belong to a
  supply_partner directly.
* No router-router tag overlap was observed -- a tag's router (if any)
  is unique.
"""

from __future__ import annotations

from typing import Any

from src.adapters.springserve._transport import SpringServeTransport


class SpringServeSupplyClient:
    """Read-only supply-side + KV-catalog client bound to one transport."""

    def __init__(self, transport: SpringServeTransport):
        self._transport = transport

    # ----- supply hierarchy -----

    def list_supply_partners(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        body = self._transport.get_json("/supply_partners", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []

    def list_supply_routers(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        body = self._transport.get_json("/supply_routers", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []

    def list_supply_tags(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        body = self._transport.get_json("/supply_tags", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []

    def list_supply_tags_in_router(
        self, supply_router_id: int | str, *, page: int = 1, per_page: int = 100
    ) -> list[dict[str, Any]]:
        """Return tags filtered to one router. The only API path that surfaces
        the tag<->router membership -- neither side of the relationship
        carries the back-reference in its own payload.
        """
        body = self._transport.get_json(
            "/supply_tags",
            supply_router_id=int(supply_router_id),
            page=page,
            per_page=per_page,
        )
        return list(body) if isinstance(body, list) else []

    # ----- KV / audience catalog -----

    def list_keys(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        """The publisher's KV namespace catalog.

        Each entry declares one targetable key (e.g. ``audience_group``,
        ``station_id``, ``device_family``) with its allowed value model
        (free-form vs constrained to a value_list).
        """
        body = self._transport.get_json("/keys", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []

    def list_value_lists(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        """Named value sets attached to a key.

        This is where publisher-curated audience segments live: a value_list
        named "Audio M25-54" attached to ``station_id`` carries the station
        IDs that comprise that demographic audience.
        """
        body = self._transport.get_json("/value_lists", page=page, per_page=per_page)
        return list(body) if isinstance(body, list) else []
