"""FreeWheel commercial client (v3 XML, ``/services/v3/*``).

Reads (advertisers, campaigns, insertion orders, placements, agencies)
and a verified write surface (create_campaign, delete_campaign). Writes
beyond campaigns require sandbox validation before exposure.

Quirks of the v3 surface:

- Plural noun (``/campaigns``) is used for GET (list + single). Singular
  (``/campaign``) is used for POST and DELETE. Both shapes are valid.
- Requests/responses are XML. The transport handles content-type
  negotiation; this layer handles XML -> Pydantic conversion.
- A v3 request without ``accept: application/xml`` returns
  ``400 json is not supported`` — see :class:`FreeWheelTransport`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any

from src.adapters.freewheel._pagination import iter_pages
from src.adapters.freewheel._transport import FreeWheelTransport
from src.adapters.freewheel.entities import (
    Advertiser,
    Agency,
    Campaign,
    InsertionOrder,
    PaginatedResponse,
    Placement,
)

DEFAULT_PER_PAGE = 50
_BASE = "/services/v3"


def _element_to_dict(element: ET.Element) -> dict[str, Any]:
    """Convert an XML element to a dict suitable for Pydantic validation.

    Recursively handles child elements. Empty leaf elements map to ``None``
    so Pydantic ``int | None`` / ``datetime | None`` fields don't fail
    coercion on a stray ``""``. Empty container elements (no children, no
    text — like ``<schedule />``) likewise become ``None`` so optional
    nested models stay optional.

    Repeated child tags would need list collapsing, but the FreeWheel v3
    shapes observed so far don't use repeated tags inside entity records.
    """
    result: dict[str, Any] = {}
    for child in element:
        if len(child) > 0:
            result[child.tag] = _element_to_dict(child)
            continue
        text = (child.text or "").strip()
        result[child.tag] = text if text else None
    return result


def _parse_collection(root: ET.Element, item_tag: str) -> dict[str, Any]:
    """Parse a v3 paginated collection envelope.

    Pagination metadata is on the root element's attributes; entries are
    child elements of ``item_tag``.
    """
    attrs = root.attrib
    items = [_element_to_dict(el) for el in root.findall(item_tag)]
    return {
        "page": int(attrs.get("current_page", 1)),
        "per_page": int(attrs.get("per_page", 0)),
        "total_count": int(attrs.get("total_entries", 0)),
        "total_page": int(attrs.get("total_pages", 1)),
        "items": items,
    }


def _build_xml(root_tag: str, fields: dict[str, Any]) -> str:
    """Serialize a flat-or-shallow-nested dict as a v3 XML request body.

    Supports one level of nesting: ``{"budget": {"budget_model": "...",
    "impression": 100}}`` becomes ``<budget><budget_model>...</budget_model>
    <impression>100</impression></budget>``. None values are skipped at any
    level so partial updates send only what they intend to change.
    """
    root = ET.Element(root_tag)
    _append_fields(root, fields)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _append_fields(parent: ET.Element, fields: dict[str, Any]) -> None:
    for key, value in fields.items():
        if value is None:
            continue
        el = ET.SubElement(parent, key)
        if isinstance(value, dict):
            _append_fields(el, value)
        else:
            el.text = str(value)


class FreeWheelCommercialClient:
    """v3 commercial client. Reads everywhere; writes on campaigns only (verified)."""

    def __init__(self, transport: FreeWheelTransport):
        self._transport = transport

    # ----- advertisers -----

    def list_advertisers(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[Advertiser]:
        root = self._transport.get_xml(f"{_BASE}/advertisers", page=page, per_page=per_page)
        return PaginatedResponse[Advertiser].model_validate(_parse_collection(root, "advertiser"))

    def get_advertiser(self, advertiser_id: int) -> Advertiser:
        root = self._transport.get_xml(f"{_BASE}/advertisers/{advertiser_id}")
        return Advertiser.model_validate(_element_to_dict(root))

    def iter_advertisers(self, per_page: int = DEFAULT_PER_PAGE) -> Iterator[Advertiser]:
        yield from iter_pages(self.list_advertisers, per_page=per_page)

    # ----- agencies -----

    def list_agencies(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[Agency]:
        root = self._transport.get_xml(f"{_BASE}/agencies", page=page, per_page=per_page)
        return PaginatedResponse[Agency].model_validate(_parse_collection(root, "agency"))

    def get_agency(self, agency_id: int) -> Agency:
        root = self._transport.get_xml(f"{_BASE}/agencies/{agency_id}")
        return Agency.model_validate(_element_to_dict(root))

    # ----- campaigns -----

    def list_campaigns(
        self,
        *,
        advertiser_id: int | None = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> PaginatedResponse[Campaign]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if advertiser_id is not None:
            params["advertiser_id"] = advertiser_id
        root = self._transport.get_xml(f"{_BASE}/campaigns", **params)
        return PaginatedResponse[Campaign].model_validate(_parse_collection(root, "campaign"))

    def get_campaign(self, campaign_id: int) -> Campaign:
        root = self._transport.get_xml(f"{_BASE}/campaigns/{campaign_id}")
        return Campaign.model_validate(_element_to_dict(root))

    def create_campaign(self, *, name: str, advertiser_id: int, **extra: Any) -> Campaign:
        """Create a campaign and return its server-assigned record.

        Minimum required body is ``name`` + ``advertiser_id`` (verified
        against the live API on 2026-05-12). Additional fields (description,
        external_id, start_date, end_date, agency_id) may be passed via
        ``extra``. Status defaults to ``IN_ACTIVE`` on the server side, so
        new campaigns do not auto-deliver.
        """
        body = _build_xml("campaign", {"name": name, "advertiser_id": advertiser_id, **extra})
        root = self._transport.post_xml(f"{_BASE}/campaign", body)
        return Campaign.model_validate(_element_to_dict(root))

    def update_campaign(self, campaign_id: int, **fields: Any) -> Campaign:
        """Partial-update a campaign via PUT (PATCH returns 405).

        Only fields provided in ``**fields`` are sent; unset fields retain
        their current value server-side. Returns the full updated campaign.
        """
        body = _build_xml("campaign", fields)
        root = self._transport.put_xml(f"{_BASE}/campaign/{campaign_id}", body)
        return Campaign.model_validate(_element_to_dict(root))

    def delete_campaign(self, campaign_id: int) -> None:
        """Hard-delete a campaign. Confirmed end-to-end against the live API."""
        self._transport.delete_xml(f"{_BASE}/campaign/{campaign_id}")

    # ----- insertion orders -----

    def list_insertion_orders(
        self,
        *,
        advertiser_id: int | None = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> PaginatedResponse[InsertionOrder]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if advertiser_id is not None:
            params["advertiser_id"] = advertiser_id
        root = self._transport.get_xml(f"{_BASE}/insertion_orders", **params)
        return PaginatedResponse[InsertionOrder].model_validate(_parse_collection(root, "insertion_order"))

    def get_insertion_order(self, insertion_order_id: int) -> InsertionOrder:
        root = self._transport.get_xml(f"{_BASE}/insertion_orders/{insertion_order_id}")
        return InsertionOrder.model_validate(_element_to_dict(root))

    def create_insertion_order(self, *, name: str, campaign_id: int, **extra: Any) -> InsertionOrder:
        """Create an insertion order under a campaign.

        Minimum required body is ``name`` + ``campaign_id`` (verified on
        2026-05-12). Server defaults: ``stage=NOT_BOOKED``, ``currency=EUR``,
        and auto-attaches an ``assigned_user`` derived from the bearer
        token's identity (ignored by our model — extra fields are dropped).
        """
        body = _build_xml("insertion_order", {"name": name, "campaign_id": campaign_id, **extra})
        root = self._transport.post_xml(f"{_BASE}/insertion_order", body)
        return InsertionOrder.model_validate(_element_to_dict(root))

    def update_insertion_order(self, insertion_order_id: int, **fields: Any) -> InsertionOrder:
        """Partial-update an insertion order via PUT. Verified 2026-05-12.

        Nested fields like ``budget`` accept a dict and serialise into the
        XML element shape FreeWheel expects::

            client.update_insertion_order(
                io_id, budget={"budget_model": "IMPRESSION_TARGET", "impression": 20000}
            )
        """
        body = _build_xml("insertion_order", fields)
        root = self._transport.put_xml(f"{_BASE}/insertion_order/{insertion_order_id}", body)
        return InsertionOrder.model_validate(_element_to_dict(root))

    def delete_insertion_order(self, insertion_order_id: int) -> None:
        """Hard-delete an insertion order. Verified end-to-end."""
        self._transport.delete_xml(f"{_BASE}/insertion_order/{insertion_order_id}")

    # ----- placements -----

    def list_placements(
        self,
        *,
        advertiser_id: int | None = None,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> PaginatedResponse[Placement]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if advertiser_id is not None:
            params["advertiser_id"] = advertiser_id
        root = self._transport.get_xml(f"{_BASE}/placements", **params)
        return PaginatedResponse[Placement].model_validate(_parse_collection(root, "placement"))

    def create_placement(self, *, name: str, insertion_order_id: int, **extra: Any) -> Placement:
        """Create a placement under an insertion order.

        Minimum required body is ``name`` + ``insertion_order_id`` (verified
        2026-05-12). Server defaults: ``status=IN_ACTIVE``,
        ``placement_type=NORMAL``.
        """
        body = _build_xml("placement", {"name": name, "insertion_order_id": insertion_order_id, **extra})
        root = self._transport.post_xml(f"{_BASE}/placement", body)
        return Placement.model_validate(_element_to_dict(root))

    def update_placement(self, placement_id: int, **fields: Any) -> Placement:
        """Partial-update a placement via PUT. Verified 2026-05-12.

        Common use case is toggling delivery state by setting ``status`` to
        ``ACTIVE`` (live) or ``IN_ACTIVE`` (paused).
        """
        body = _build_xml("placement", fields)
        root = self._transport.put_xml(f"{_BASE}/placement/{placement_id}", body)
        return Placement.model_validate(_element_to_dict(root))

    def delete_placement(self, placement_id: int) -> None:
        """Hard-delete a placement. Verified end-to-end."""
        self._transport.delete_xml(f"{_BASE}/placement/{placement_id}")

    def get_placement(self, placement_id: int) -> Placement:
        root = self._transport.get_xml(f"{_BASE}/placements/{placement_id}")
        return Placement.model_validate(_element_to_dict(root))
