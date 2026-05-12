"""FreeWheel creative client (v4 JSON, ``/services/v4/creative_resources``).

Manages the publisher-side creative records: the named wrappers around
VAST tag URIs (or hosted assets) that advertisers can deliver. Renditions
— the actual ad payload references — are nested under each creative and
returned inline when ``?include=renditions`` is set on the request.

Scope situation (verified against a publisher test network 2026-05-12):

  - ✅ ``/services/v4/creative_resources``                 — full CRUD verified
  - ❌ ``/services/v4/creative_instances``                 — 403 (creative ↔
                                                            placement linkage)
  - ❌ ``/services/v4/creative_renditions`` (standalone)   — 403

So this client can create, read, update and delete creatives, but
attaching them to a placement (so they actually deliver) requires the
publisher to grant scope on ``creative_instances`` separately.
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


def _unwrap_creative(envelope: dict[str, Any]) -> dict[str, Any]:
    """Single-creative endpoints wrap the entity in ``{"creative": {...}}``;
    list endpoints flatten it. This unwraps the envelope when present."""
    return envelope.get("creative", envelope)


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
