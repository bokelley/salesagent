"""Pydantic models for SpringServe upstream API entities.

Captures only the fields the adapter reads, writes, or asserts on. The
real API responses are far richer (Campaign carries ~38 fields, Demand
Tag ~220) -- we use ``extra="allow"`` so unknown fields round-trip
without modification rather than erroring out. New fields the adapter
needs get promoted to typed attributes in subsequent stages.

Field shapes captured from live probes against the Magnite production
host on 2026-05-14; see ``docs/adapters/springserve/`` for the live
coverage matrix.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _SpringServeEntity(BaseModel):
    """Common base: allow unknown upstream fields, accept str/int IDs."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Campaign(_SpringServeEntity):
    """SpringServe Campaign -- the commercial container for an AdCP MediaBuy.

    Wraps N Demand Tags via ``demand_tag_ids``. Carries scheduling, naming,
    and demand-partner scoping but NOT pricing (rate lives on each demand
    tag). ``is_active`` flips the whole campaign on/off.
    """

    id: int
    name: str
    account_id: int | None = None
    demand_partner_id: int
    is_active: bool = False
    is_managed: bool = False
    code: str | None = None
    secondary_code: str | None = None
    note: str | None = None
    rate: str | None = None  # SS encodes rate as a string ("27.0")
    rate_currency: str | None = None
    cost_model_type: int | None = None
    demand_tag_ids: list[int] = Field(default_factory=list)
    guaranteed_delivery: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DemandTag(_SpringServeEntity):
    """SpringServe Demand Tag -- the per-package delivery unit.

    One per AdCP Package. Carries the rate, the supply-targeting via
    ``demand_tag_priorities``, the flight dates, the targeting filters
    applied directly on the tag (country_codes / state_codes / metro /
    player_sizes / device targeting), and the creative binding via
    ``creative_id`` or ``line_item_ratios``.
    """

    id: int
    name: str
    campaign_id: int
    account_id: int | None = None
    demand_partner_id: int
    is_active: bool = False
    active: bool = False  # SS exposes both -- read-only mirror of is_active
    rate: str | None = None
    rate_currency: str | None = None
    cost_model_type: int | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    format: Literal["video", "audio", "display"] | None = None
    creative_id: int | None = None
    line_item_ratios: list[dict] = Field(default_factory=list)
    demand_tag_priorities: list[dict] = Field(default_factory=list)
    budgets: list[dict] = Field(default_factory=list)
    demand_code: str | None = None
    secondary_code: str | None = None
    note: str | None = None
    # Geo targeting (flattened on the tag, NOT wrapped in a sub-object)
    country_codes: list[str] = Field(default_factory=list)
    country_targeting: str = "All"
    state_codes: list[str] = Field(default_factory=list)
    state_targeting: str = "All"
    metro_area_codes: list[str] = Field(default_factory=list)
    metro_area_targeting: str = "All"
    # Player + device
    player_sizes: list[str] = Field(default_factory=list)
    player_size_targeting: str = "All"
    user_agent_devices: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VideoCreative(_SpringServeEntity):
    """SpringServe creative -- video OR audio.

    The ``/videos`` endpoint hosts both media types, discriminated by
    ``creative_format`` ("video" | "audio") and ``creative_content_type``.
    The ``type`` field returned by SpringServe is the entity class
    ("VideoCreative") regardless of media type.
    """

    id: int
    name: str
    account_id: int | None = None
    demand_partner_id: int
    creative_format: Literal["video", "audio"] = "video"
    creative_content_type: str | None = None
    creative_remote_url: str | None = None
    creative_file_name: str | None = None
    creative_file_size: int | None = None
    creative_landing_page_url: str | None = None
    type: str | None = None  # SS-internal entity class, e.g. "VideoCreative"
    active: bool = True
    duration_seconds: int | None = None
    height: int | None = None
    width: int | None = None
    line_item_demand_tag_ids: list[int] = Field(default_factory=list)
    secondary_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DemandTagPriority(BaseModel):
    """One ``demand_tag_priorities`` entry binding a Demand Tag to a Supply Tag.

    SpringServe stores supply targeting as an ordered list of priorities on
    the demand tag. ``priority`` orders multiple supply tags within the
    same demand tag (lower = preferred). ``tier`` is the SpringServe demand
    tier (1 = standard direct).
    """

    model_config = ConfigDict(extra="forbid")

    supply_tag_id: int
    priority: int = 1
    tier: int = 1
