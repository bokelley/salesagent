"""Pydantic schemas for the Embedded Composition API (``/api/v1/...``).

Server-to-server REST surface that lets an embedding storefront compose
products from primitives — inventory profile + signal selections + price.
See ``.context/embedded-composition-design.md``.

Adapter-agnostic at the storefront boundary:
- ``InventoryProfileRead`` (what storefront sees) carries only AdCP-vocab
  metadata. Adapter-specific config is operator-authored on Create/Update
  and never echoed in storefront-facing reads.
- ``TenantSignalRead`` mirrors AdCP's existing ``Signal`` shape
  (``value_type``, ``categories``, ``range``). Adapter-specific resolution
  lives in ``adapter_config`` on Create/Update only.

Composition body uses ``signal_selections`` (a buyer-style selection over
operator-declared signals), not opaque profile references — so the
storefront can compose with the same vocabulary it uses for AdCP signals
elsewhere.

All schemas follow the project ``get_pydantic_extra_mode()`` convention:
forbid unknown fields in dev/CI, ignore them in production.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import AdCPPricingOption

_EXTRA_MODE = get_pydantic_extra_mode()


def _config() -> ConfigDict:
    return ConfigDict(extra=_EXTRA_MODE)


# ---------------------------------------------------------------------------
# Capability narrowings (AdCP-vocab; storefront-visible)
# ---------------------------------------------------------------------------


class ProfileConstraints(BaseModel):
    """Typed AdCP capability narrowings on an inventory profile.

    Vocabulary references the agent's declared ``DecisioningCapabilities``;
    profiles express narrowings, never redeclarations. Lets the storefront
    pre-validate ``(inventory ∩ signal_selections ∩ buyer_targeting)``
    client-side.
    """

    model_config = _config()

    formats: list[str] = Field(default_factory=list, description="Allowed AdCP format ids")
    channels: list[str] = Field(default_factory=list, description="Allowed AdCP channel names")
    targeting_dimensions: list[str] = Field(
        default_factory=list,
        description="AdCP-standard targeting-dimension names usable on this inventory",
    )


# ---------------------------------------------------------------------------
# Inventory profiles
# ---------------------------------------------------------------------------


class InventoryProfileCreate(BaseModel):
    """Operator-authored. Includes the adapter-shaped ``inventory_config``
    blob (GAM placements, FW sites, Broadstreet zones, …) and the
    AdCP-vocab ``constraints`` narrowings."""

    model_config = _config()

    profile_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    inventory_config: dict = Field(
        default_factory=dict,
        description=(
            "Adapter-specific inventory selection. Operator-authored, opaque to the "
            "storefront. Shape depends on tenant.ad_server (GAM: {ad_units, placements, "
            "include_descendants}; Freewheel: {site_ids, video_group_ids, ...}; "
            "Broadstreet: {zone_ids}; etc.)."
        ),
    )
    format_ids: list[dict] = Field(default_factory=list)
    publisher_properties: list[dict] = Field(default_factory=list)
    targeting_template: dict | None = None
    constraints: ProfileConstraints | None = None


class InventoryProfileUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    inventory_config: dict | None = None
    format_ids: list[dict] | None = None
    publisher_properties: list[dict] | None = None
    targeting_template: dict | None = None
    constraints: ProfileConstraints | None = None


class InventoryProfileRead(BaseModel):
    """Storefront-facing read. AdCP-vocab metadata only — no adapter-shaped
    fields. Operators that need to inspect the underlying ``inventory_config``
    can use the admin UI; this surface is for storefront discovery and
    composition."""

    model_config = _config()

    profile_id: str
    name: str
    description: str | None
    constraints: ProfileConstraints | None
    etag: str | None
    created_at: datetime
    updated_at: datetime


class InventoryProfileListResponse(BaseModel):
    model_config = _config()
    inventory_profiles: list[InventoryProfileRead]


# ---------------------------------------------------------------------------
# Tenant signals — operator's map of adapter targeting capabilities
# ---------------------------------------------------------------------------


_SIGNAL_VALUE_TYPE = Literal["binary", "categorical", "numeric"]


class SignalRange(BaseModel):
    """Numeric bounds for a ``value_type='numeric'`` signal."""

    model_config = _config()

    min: Decimal | None = None
    max: Decimal | None = None


class TenantSignalCreate(BaseModel):
    """Operator-authored. ``adapter_config`` is the opaque resolution map
    consumed by the per-adapter materializer at compose time."""

    model_config = _config()

    signal_id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    value_type: _SIGNAL_VALUE_TYPE
    categories: list[str] = Field(default_factory=list, description="Taxonomy when value_type='categorical'")
    range: SignalRange | None = Field(default=None, description="Bounds when value_type='numeric'")
    adapter_config: dict = Field(
        default_factory=dict,
        description=(
            "Adapter-specific resolution map. Operator-authored, opaque to storefront. "
            "Examples: GAM custom KV → {kind: 'custom_key_value', key_id: '...', value_ids: {...}}; "
            "GAM audience → {kind: 'audience_segment', segment_id: '...'}; "
            "Freewheel → {kind: 'audience_item', audience_item_id: '...'}."
        ),
    )
    data_provider: str | None = None
    targeting_dimension: str | None = Field(
        default=None,
        description="AdCP-standard dimension this signal narrows (audience, contextual, weather, ...)",
    )


class TenantSignalUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    value_type: _SIGNAL_VALUE_TYPE | None = None
    categories: list[str] | None = None
    range: SignalRange | None = None
    adapter_config: dict | None = None
    data_provider: str | None = None
    targeting_dimension: str | None = None


class TenantSignalRead(BaseModel):
    """Storefront-facing read. Mirrors AdCP ``Signal`` vocabulary — no
    ``adapter_config`` echo. Storefront uses ``value_type`` + ``categories`` /
    ``range`` to render UI."""

    model_config = _config()

    signal_id: str
    name: str
    description: str | None
    value_type: _SIGNAL_VALUE_TYPE
    categories: list[str]
    range: SignalRange | None
    data_provider: str | None
    targeting_dimension: str | None
    etag: str | None
    created_at: datetime
    updated_at: datetime


class TenantSignalListResponse(BaseModel):
    model_config = _config()
    signals: list[TenantSignalRead]


# ---------------------------------------------------------------------------
# Signal selections (storefront → sales agent on compose)
# ---------------------------------------------------------------------------


class SignalSelection(BaseModel):
    """One storefront-issued selection over an operator-declared signal.

    Shape echoes how AdCP buyers narrow on signals on ``create_media_buy``:
    a ``signal_id`` plus value(s) appropriate to the signal's ``value_type``.
    Include vs exclude is expressed via ``mode``; the per-adapter materializer
    translates to the adapter's native operator (GAM ``IS``/``IS_NOT``,
    Freewheel inclusion lists vs exclusion lists, …).
    """

    model_config = _config()

    signal_id: str = Field(..., min_length=1, max_length=200)
    mode: Literal["include", "exclude"] = "include"
    # For value_type='categorical': pick from signal.categories.
    values: list[str] = Field(default_factory=list)
    # For value_type='numeric': bounded range (any field optional → unbounded that side).
    range: SignalRange | None = None
    # For value_type='binary': True = signal applies, False = signal explicitly off.
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Advertiser mappings (operator + brand → adapter advertiser routing)
# ---------------------------------------------------------------------------


class AccountBrandPattern(BaseModel):
    """Brand component of an :class:`AccountPattern`. Same field names as AdCP
    ``BrandReference`` (``domain``, ``brand_id``) but both are optional so a
    routing rule can wildcard the brand house, the brand id, or both."""

    model_config = _config()

    domain: str | None = Field(default=None, max_length=255)
    brand_id: str | None = Field(default=None, max_length=255)


class AccountPattern(BaseModel):
    """Routing-rule pattern over an AdCP account.

    Structurally mirrors ``AccountReference`` (operator + brand + sandbox)
    so the storefront can paste the same shape it uses on
    ``create_media_buy`` — but ``brand`` is optional here, and components
    inside ``brand`` are individually optional, because routing rules
    treat NULL columns as wildcards per the existing resolution chain
    (exact → house wildcard → operator wildcard → tenant default).

    Strict AccountReference targeting one account uses every field; routing
    patterns may leave some absent.
    """

    model_config = _config()

    operator: str = Field(..., min_length=1, max_length=255)
    brand: AccountBrandPattern | None = None
    sandbox: bool = False


class AdvertiserMappingCreate(BaseModel):
    """Route buys carrying ``account: AccountReference`` to a specific adapter
    advertiser. The natural key is ``account`` (operator + brand + sandbox);
    NULL components on ``brand`` act as wildcards.
    """

    model_config = _config()

    account: AccountPattern
    adapter_advertiser_id: str = Field(..., min_length=1, max_length=64)


class AdvertiserMappingUpdate(BaseModel):
    model_config = _config()

    adapter_advertiser_id: str | None = Field(default=None, min_length=1, max_length=64)


class AdvertiserMappingRead(BaseModel):
    model_config = _config()

    mapping_id: str
    account: AccountPattern
    adapter_advertiser_id: str
    created_at: datetime
    updated_at: datetime


class AdvertiserMappingListResponse(BaseModel):
    model_config = _config()
    advertiser_mappings: list[AdvertiserMappingRead]


class AdvertiserSummary(BaseModel):
    """Entry in the synced adapter-advertiser cache. Read-only mirror of the
    operator's GAM (or other adapter) advertiser list."""

    model_config = _config()

    adapter_advertiser_id: str
    name: str
    status: str
    currency_code: str | None = None
    synced_at: datetime


class AdvertiserListResponse(BaseModel):
    model_config = _config()
    advertisers: list[AdvertiserSummary]


# ---------------------------------------------------------------------------
# Dynamic product composition
# ---------------------------------------------------------------------------


class DynamicProductCreate(BaseModel):
    """Body for ``POST /api/v1/products``. Idempotency-Key header is required.

    Pricing flows through AdCP's typed ``PricingOption`` — same wire shape
    ``create_media_buy`` and ``get_products`` use. Targeting flows through
    ``signal_selections`` over the operator's declared signals. No
    adapter-specific fields on this surface.
    """

    model_config = _config()

    inventory_profile_id: str = Field(..., min_length=1)
    signal_selections: list[SignalSelection] = Field(default_factory=list)
    adcp_targeting: dict | None = Field(
        default=None,
        description="Optional AdCP-native targeting baked into the product at compose time",
    )
    pricing_option: AdCPPricingOption = Field(
        ...,
        description="AdCP PricingOption — drives both wire shape and the persisted PricingOption row",
    )
    flight_start: date
    flight_end: date
    ttl_seconds: int | None = Field(
        default=None,
        gt=0,
        description="Storefront-requested validity; clamped by tenant.max_composition_ttl_seconds and flight_start",
    )


class DynamicProductRead(BaseModel):
    model_config = _config()

    product_id: str
    composition_source: Literal["storefront_composed"]
    inventory_profile_id: str
    signal_selections: list[SignalSelection]
    pricing_option: AdCPPricingOption
    flight_start: date
    flight_end: date
    expires_at: datetime
    implementation_config_summary: dict
    created_at: datetime


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------


_COMPOSITION_ERROR_CODES = Literal[
    "format_mismatch",
    "channel_mismatch",
    "dimension_unsupported",
    "unknown_inventory_profile",
    "unknown_signal",
    "expired_inventory_profile",
    "invalid_flight_dates",
    "unsupported_pricing_model",
    "unsupported_adapter",
    "invalid_signal_selection",
]


class CompositionErrorDetail(BaseModel):
    """Single typed entry in a composition_invalid error response."""

    model_config = _config()

    code: _COMPOSITION_ERROR_CODES
    inventory_profile_id: str | None = None
    signal_id: str | None = None
    dimension: str | None = None
    format_id: str | None = None
    channel: str | None = None
    pricing_model: str | None = None
    adapter: str | None = None
    expected: str | None = None
    got: str | None = None
    message: str | None = None


class CompositionError(BaseModel):
    """422 response shape for ``POST /api/v1/products``."""

    model_config = _config()

    error: Literal["composition_invalid"] = "composition_invalid"
    message: str = "Composition could not be materialized."
    details: list[CompositionErrorDetail]


class ApiError(BaseModel):
    """Generic error envelope mirroring ``tenant_management.ApiError``."""

    model_config = _config()

    error: str
    message: str
    details: dict | None = None
