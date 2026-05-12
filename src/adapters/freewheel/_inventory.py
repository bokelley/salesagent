"""FreeWheel inventory client (v4 JSON, ``/services/v4/*``).

Read-only access to a publisher's inventory taxonomy: sites, sections,
groups, series, videos, and pre-built inventory packages. Used for
catalog discovery and for AdCP ``Property``/``PropertyTag`` derivation.

Writes are not exposed on the v4 surface; commercial writes go through
:class:`FreeWheelCommercialClient`.
"""

from __future__ import annotations

from collections.abc import Iterator

from src.adapters.freewheel._pagination import iter_pages
from src.adapters.freewheel._transport import FreeWheelTransport
from src.adapters.freewheel.entities import (
    InventoryPackage,
    PaginatedResponse,
    Series,
    Site,
    SiteGroup,
    SiteSection,
    Video,
    VideoGroup,
)

DEFAULT_PER_PAGE = 50
_BASE = "/services/v4"


class FreeWheelInventoryClient:
    """v4 inventory client. All endpoints are read-only with this token class."""

    def __init__(self, transport: FreeWheelTransport):
        self._transport = transport

    # ----- sites -----

    def list_sites(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[Site]:
        body = self._transport.get_json(f"{_BASE}/sites", page=page, per_page=per_page)
        return PaginatedResponse[Site].model_validate(body)

    def get_site(self, site_id: int) -> Site:
        return Site.model_validate(self._transport.get_json(f"{_BASE}/sites/{site_id}"))

    def iter_sites(self, per_page: int = DEFAULT_PER_PAGE) -> Iterator[Site]:
        yield from iter_pages(self.list_sites, per_page=per_page)

    # ----- site sections -----

    def list_site_sections(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[SiteSection]:
        body = self._transport.get_json(f"{_BASE}/site_sections", page=page, per_page=per_page)
        return PaginatedResponse[SiteSection].model_validate(body)

    def get_site_section(self, section_id: int) -> SiteSection:
        return SiteSection.model_validate(self._transport.get_json(f"{_BASE}/site_sections/{section_id}"))

    # ----- site groups -----

    def list_site_groups(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[SiteGroup]:
        body = self._transport.get_json(f"{_BASE}/site_groups", page=page, per_page=per_page)
        return PaginatedResponse[SiteGroup].model_validate(body)

    def get_site_group(self, group_id: int) -> SiteGroup:
        return SiteGroup.model_validate(self._transport.get_json(f"{_BASE}/site_groups/{group_id}"))

    # ----- series -----

    def list_series(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[Series]:
        body = self._transport.get_json(f"{_BASE}/series", page=page, per_page=per_page)
        return PaginatedResponse[Series].model_validate(body)

    def get_series(self, series_id: int) -> Series:
        return Series.model_validate(self._transport.get_json(f"{_BASE}/series/{series_id}"))

    # ----- videos -----

    def list_videos(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[Video]:
        body = self._transport.get_json(f"{_BASE}/videos", page=page, per_page=per_page)
        return PaginatedResponse[Video].model_validate(body)

    def get_video(self, video_id: int) -> Video:
        return Video.model_validate(self._transport.get_json(f"{_BASE}/videos/{video_id}"))

    # ----- video groups -----

    def list_video_groups(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> PaginatedResponse[VideoGroup]:
        body = self._transport.get_json(f"{_BASE}/video_groups", page=page, per_page=per_page)
        return PaginatedResponse[VideoGroup].model_validate(body)

    def get_video_group(self, group_id: int) -> VideoGroup:
        return VideoGroup.model_validate(self._transport.get_json(f"{_BASE}/video_groups/{group_id}"))

    # ----- inventory packages -----

    def list_inventory_packages(
        self, page: int = 1, per_page: int = DEFAULT_PER_PAGE
    ) -> PaginatedResponse[InventoryPackage]:
        body = self._transport.get_json(f"{_BASE}/inventory_packages", page=page, per_page=per_page)
        return PaginatedResponse[InventoryPackage].model_validate(body)
