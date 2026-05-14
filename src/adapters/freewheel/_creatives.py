"""FreeWheel creative client (v4 JSON, ``/services/v4/creative_resources``
and ``/services/v4/creative_instances``).

Manages two related concepts:

* **Creative resource** — the publisher-side creative record (named
  wrapper around a VAST tag URI or hosted asset). Renditions are nested
  under each creative and returned inline when ``?include=renditions``.
* **Creative instance** — the (creative ↔ ad_unit_node) binding that
  actually makes the creative deliver. FW's docs name the body param
  ``ad_id`` but its description says "The Ad Unit Node ID to link
  Creative" — there's no separate Ad object. POST returns ``201`` with
  ``placement_id`` auto-populated.

Scope situation (verified against a publisher test network 2026-05-13):

  - ✅ ``/services/v4/creative_resources``  — full CRUD verified
  - ✅ ``/services/v4/creative_instances``  — POST + DELETE verified (201)
  - ❌ ``/services/v4/creative_renditions`` (standalone) — 403; renditions
        ride inline on creative_resources for our use case
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.adapters.freewheel._pagination import iter_pages
from src.adapters.freewheel._transport import FreeWheelTransport
from src.adapters.freewheel.entities import Creative, PaginatedResponse

DEFAULT_PER_PAGE = 20
_BASE = "/services/v4"
_RESOURCE = "creative_resources"
_INSTANCES = "creative_instances"


def _unwrap_creative(envelope: dict[str, Any]) -> dict[str, Any]:
    """Unwrap FW's varied creative response shapes:

    - GET single:  ``{"creative": {...}}``  (single layer)
    - POST create: ``{"data": {"success": ..., "creative": {...}}}``  (nested under data)
    - GET list:    items flatten via the PaginatedResponse parser

    Walks both shapes; falls back to the raw envelope if neither
    wrapper key is present.
    """
    if "creative" in envelope:
        return envelope["creative"]
    data = envelope.get("data")
    if isinstance(data, dict) and "creative" in data:
        return data["creative"]
    return envelope


class FreeWheelCreativeClient:
    """v4 creative client. Full CRUD verified.

    Creative ↔ placement association is not exposed here because the
    ``creative_instances`` endpoint is gated by a different scope we
    don't currently hold.
    """

    def __init__(self, transport: FreeWheelTransport):
        self._transport = transport

    def list_creatives(
        self,
        *,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
        include_renditions: bool = False,
    ) -> PaginatedResponse[Creative]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if include_renditions:
            params["include"] = "renditions"
        body = self._transport.get_json(f"{_BASE}/{_RESOURCE}", **params)
        return PaginatedResponse[Creative].model_validate(body)

    def get_creative(self, creative_id: int, *, include_renditions: bool = False) -> Creative:
        params: dict[str, Any] = {}
        if include_renditions:
            params["include"] = "renditions"
        body = self._transport.get_json(f"{_BASE}/{_RESOURCE}/{creative_id}", **params)
        return Creative.model_validate(_unwrap_creative(body))

    def iter_creatives(
        self, per_page: int = DEFAULT_PER_PAGE, *, include_renditions: bool = False
    ) -> Iterator[Creative]:
        yield from iter_pages(
            lambda page, per_page: self.list_creatives(
                page=page, per_page=per_page, include_renditions=include_renditions
            ),
            per_page=per_page,
        )

    def create_creative(
        self,
        *,
        name: str,
        advertiser_ids: list[int] | None = None,
        base_ad_unit_id: int | None = None,
        external_id: str | None = None,
        renditions: list[dict[str, Any]] | None = None,
        **extra: Any,
    ) -> Creative:
        """POST a new creative_resource and return the parsed entity.

        FW expects the body wrapped under ``{"creative": {...}}`` for
        writes (verified live — flat body returns 400 "Creative Node is
        missing"). The response unwraps the same envelope.

        ``renditions`` may include inline rendition records (VAST tag URIs,
        hosted MP4 references, etc.) — FW persists them atomically with
        the creative when set. ``extra`` lets callers pass additional
        publisher-specific fields (``duration``, ``rating_id``, etc.)
        without bloating the signature.
        """
        creative: dict[str, Any] = {"name": name}
        if advertiser_ids:
            creative["advertiser_ids"] = list(advertiser_ids)
        if base_ad_unit_id is not None:
            creative["base_ad_unit_id"] = base_ad_unit_id
        if external_id is not None:
            creative["external_id"] = external_id
        if renditions:
            creative["renditions"] = list(renditions)
        creative.update(extra)
        response = self._transport.post_json(f"{_BASE}/{_RESOURCE}", {"creative": creative})
        return Creative.model_validate(_unwrap_creative(response))

    def delete_creative(self, creative_id: int) -> None:
        """DELETE a creative_resource. Used for cleanup in live smoke tests
        and operator-triggered removal."""
        self._transport.delete_json(f"{_BASE}/{_RESOURCE}/{creative_id}")

    def create_creative_instance(
        self,
        *,
        ad_unit_node_id: int,
        creative_id: int,
        tracking_name: str | None = None,
    ) -> dict[str, Any]:
        """Bind a creative to an ad_unit_node (FW's term for the
        placement-to-inventory binding row). FW's docs call the param
        ``ad_id`` but its description says "The Ad Unit Node ID to link
        Creative" — there's no separate Ad concept.

        Returns the raw response dict (parsed JSON) — it includes the
        auto-populated ``placement_id`` so callers can persist the
        binding lineage if useful.
        """
        body: dict[str, Any] = {"ad_id": ad_unit_node_id, "creative_id": creative_id}
        if tracking_name is not None:
            body["tracking_name"] = tracking_name
        return self._transport.post_json(f"{_BASE}/{_INSTANCES}", body)

    def delete_creative_instance(self, instance_id: int) -> None:
        """DELETE a creative_instance — un-traffics the creative from the
        ad_unit_node. Verified live: returns 200 with empty body."""
        self._transport.delete_json(f"{_BASE}/{_INSTANCES}/{instance_id}")
