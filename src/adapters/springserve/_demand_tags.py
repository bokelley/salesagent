"""Typed CRUD over the SpringServe Demand Tags API.

Endpoint reference:
- POST   /api/v0/demand_tags
- GET    /api/v0/demand_tags/{id}
- PUT    /api/v0/demand_tags/{id}
- DELETE /api/v0/demand_tags/{id}

The Demand Tag is the per-Package delivery unit. It carries rate,
flight dates, geo + device + player targeting (flattened onto the tag,
NOT wrapped in a sub-object), and supply targeting via
``demand_tag_priorities``. Creatives bind to the tag via ``creative_id``
(single) or ``line_item_ratios`` (rotation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.adapters.springserve._transport import SpringServeTransport
from src.adapters.springserve.entities import DemandTag


def _format_ss_datetime(value: datetime) -> str:
    """Format datetime in SpringServe's expected wire format.

    SpringServe uses ISO 8601 with microseconds and a literal ``Z`` suffix
    for UTC (e.g. ``2026-02-10T00:00:00.000000Z``). Naive datetimes are
    assumed to be UTC; aware datetimes are converted.
    """
    if value.tzinfo is not None:
        # Convert to UTC and strip tzinfo for the .Z suffix
        from datetime import UTC

        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class SpringServeDemandTagsClient:
    """Demand Tag CRUD bound to one :class:`SpringServeTransport`."""

    def __init__(self, transport: SpringServeTransport):
        self._transport = transport

    def create(
        self,
        *,
        name: str,
        campaign_id: int,
        demand_partner_id: int,
        start_date: datetime,
        end_date: datetime,
        format: str = "video",
        rate: float | str | None = None,
        rate_currency: str = "USD",
        cost_model_type: int = 0,
        is_active: bool = False,
        demand_code: str | None = None,
        secondary_code: str | None = None,
        note: str | None = None,
        country_codes: list[str] | None = None,
        country_targeting: str = "All",
        state_codes: list[str] | None = None,
        state_targeting: str = "All",
        metro_area_codes: list[str] | None = None,
        metro_area_targeting: str = "All",
        player_sizes: list[str] | None = None,
        player_size_targeting: str = "All",
        user_agent_devices: list[str] | None = None,
        demand_tag_priorities: list[dict] | None = None,
        **extras: Any,
    ) -> DemandTag:
        """POST a new Demand Tag and return the parsed entity.

        Tags are created paused (``is_active=False``); flip them via
        :meth:`update` once a creative is bound. ``demand_tag_priorities``
        carries supply-tag targeting (``[{"supply_tag_id": ..., "priority":
        1, "tier": 1}, ...]``).
        """
        body: dict[str, Any] = {
            "name": name,
            "campaign_id": campaign_id,
            "demand_partner_id": demand_partner_id,
            "start_date": _format_ss_datetime(start_date),
            "end_date": _format_ss_datetime(end_date),
            "format": format,
            "rate_currency": rate_currency,
            "cost_model_type": cost_model_type,
            "is_active": is_active,
        }
        if rate is not None:
            # SpringServe encodes rate as a string at rest; accept float or string.
            body["rate"] = str(rate)
        if demand_code is not None:
            body["demand_code"] = demand_code
        if secondary_code is not None:
            body["secondary_code"] = secondary_code
        if note is not None:
            body["note"] = note
        if country_codes:
            body["country_codes"] = list(country_codes)
            body["country_targeting"] = country_targeting if country_targeting != "All" else "White List"
        elif country_targeting != "All":
            body["country_targeting"] = country_targeting
        if state_codes:
            body["state_codes"] = list(state_codes)
            body["state_targeting"] = state_targeting if state_targeting != "All" else "White List"
        if metro_area_codes:
            body["metro_area_codes"] = list(metro_area_codes)
            body["metro_area_targeting"] = metro_area_targeting if metro_area_targeting != "All" else "White List"
        if player_sizes:
            body["player_sizes"] = list(player_sizes)
            body["player_size_targeting"] = player_size_targeting if player_size_targeting != "All" else "White List"
        if user_agent_devices:
            body["user_agent_devices"] = list(user_agent_devices)
        if demand_tag_priorities:
            body["demand_tag_priorities"] = list(demand_tag_priorities)
        body.update(extras)
        response = self._transport.post_json("/demand_tags", body)
        return DemandTag.model_validate(response)

    def get(self, demand_tag_id: int) -> DemandTag:
        response = self._transport.get_json(f"/demand_tags/{demand_tag_id}")
        return DemandTag.model_validate(response)

    def update(self, demand_tag_id: int, *, is_active: bool | None = None, **fields: Any) -> DemandTag:
        """PUT changes to a Demand Tag.

        ``is_active`` is the most common toggle (per-package pause/resume).
        Other supported fields: ``rate``, ``end_date``, ``creative_id``,
        ``country_codes``, ``demand_tag_priorities``, etc.
        """
        body: dict[str, Any] = dict(fields)
        if is_active is not None:
            body["is_active"] = is_active
        response = self._transport.put_json(f"/demand_tags/{demand_tag_id}", body)
        return DemandTag.model_validate(response)

    def delete(self, demand_tag_id: int) -> None:
        self._transport.delete_json(f"/demand_tags/{demand_tag_id}")

    def add_kv_entry(
        self,
        demand_tag_id: int,
        *,
        key_id: str | int,
        list_type: str,
        group: str = "1",
        free_values: list[str] | None = None,
        value_ids: list[int] | None = None,
        value_list_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """POST one Key-Value targeting entry to the demand tag's sub-resource.

        Endpoint: ``POST /api/v0/demand_tags/<demand_tag_id>/demand_tag_keys``
        (SpringServe docs page 1628471383). Returns the created entry's
        record (with its own ``id`` so the entry can be PUT/DELETE'd
        later via the same path + entry id).

        Grouping: same ``group`` = AND, different ``group`` = OR. Within
        an entry the value array is OR.

        Caller must first ensure the parent demand_tag has
        ``key_value_targeting=true`` set -- the sub-resource POST rejects
        with HTTP 422 "Targeter must have key_value_targeting set to
        true" otherwise. That flag is not currently writable via the v0
        API on AdOps-tier accounts; see ``targeting.py`` module docstring.
        """
        body: dict[str, Any] = {
            "key_id": str(key_id),
            "list_type": list_type,
            "group": group,
        }
        if free_values:
            body["free_values"] = list(free_values)
        if value_ids:
            body["value_ids"] = list(value_ids)
        if value_list_ids:
            body["value_list_ids"] = list(value_list_ids)
        return self._transport.post_json(f"/demand_tags/{demand_tag_id}/demand_tag_keys", body)
