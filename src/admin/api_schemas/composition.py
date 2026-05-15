"""Pydantic schemas for the Embedded Composition API (`/api/v1/...`).

Server-to-server REST surface that lets an embedding storefront compose
products from primitives (inventory profile + custom targeting profile +
agreed price). See ``.context/embedded-composition-design.md``.

All schemas follow the project ``get_pydantic_extra_mode()`` convention:
forbid unknown fields in dev/CI, ignore them in production.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import get_pydantic_extra_mode

_EXTRA_MODE = get_pydantic_extra_mode()


def _config() -> ConfigDict:
    return ConfigDict(extra=_EXTRA_MODE)


# ---------------------------------------------------------------------------
# Capability narrowings
# ---------------------------------------------------------------------------


class ProfileConstraints(BaseModel):
    """Typed AdCP capability narrowings on an inventory profile or targeting profile.

    Vocabulary references the agent's declared ``DecisioningCapabilities``;
    profiles express narrowings, never redeclarations. Lets the storefront
    pre-validate ``(inventory ∩ targeting ∩ buyer_request)`` client-side.
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
    model_config = _config()

    profile_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    inventory_config: dict = Field(
        default_factory=dict,
        description='{"ad_units": [...], "placements": [...], "include_descendants": bool}',
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
    model_config = _config()

    profile_id: str
    name: str
    description: str | None
    inventory_config: dict
    format_ids: list[dict]
    publisher_properties: list[dict]
    targeting_template: dict | None
    constraints: ProfileConstraints | None
    etag: str | None
    created_at: datetime
    updated_at: datetime


class InventoryProfileListResponse(BaseModel):
    model_config = _config()
    inventory_profiles: list[InventoryProfileRead]


# ---------------------------------------------------------------------------
# Custom targeting profiles
# ---------------------------------------------------------------------------


_MODE = Literal["include", "exclude"]


class KeyValueComponent(BaseModel):
    model_config = _config()

    key: str = Field(..., min_length=1, max_length=200)
    values: list[str] = Field(..., min_length=1)
    mode: _MODE = "include"


class AudienceSegmentComponent(BaseModel):
    model_config = _config()

    segment_id: str = Field(..., min_length=1)
    mode: _MODE = "include"


class TargetingComponents(BaseModel):
    """Adapter-specific overlays only. AdCP-native targeting (geo, daypart, etc.)
    flows through the create_media_buy request — not here.
    """

    model_config = _config()

    key_values: list[KeyValueComponent] = Field(default_factory=list)
    audience_segments: list[AudienceSegmentComponent] = Field(default_factory=list)


class CustomTargetingProfileCreate(BaseModel):
    model_config = _config()

    custom_targeting_profile_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    components: TargetingComponents = Field(default_factory=TargetingComponents)
    touches_dimensions: list[str] = Field(
        default_factory=list,
        description="AdCP-standard dimension names this profile narrows",
    )


class CustomTargetingProfileUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    components: TargetingComponents | None = None
    touches_dimensions: list[str] | None = None


class CustomTargetingProfileRead(BaseModel):
    model_config = _config()

    custom_targeting_profile_id: str
    name: str
    description: str | None
    components: TargetingComponents
    touches_dimensions: list[str]
    etag: str | None
    created_at: datetime
    updated_at: datetime


class CustomTargetingProfileListResponse(BaseModel):
    model_config = _config()
    custom_targeting_profiles: list[CustomTargetingProfileRead]


# ---------------------------------------------------------------------------
# Discovery (raw adapter primitives)
# ---------------------------------------------------------------------------


class CustomTargetingKey(BaseModel):
    model_config = _config()

    key: str
    display_name: str | None = None
    description: str | None = None
    value_count: int | None = None


class CustomTargetingKeyValue(BaseModel):
    model_config = _config()

    value: str
    display_name: str | None = None


class AudienceSegment(BaseModel):
    model_config = _config()

    segment_id: str
    name: str
    description: str | None = None
    size: int | None = None
    active: bool = True


class CustomTargetingKeysResponse(BaseModel):
    model_config = _config()
    custom_targeting_keys: list[CustomTargetingKey]


class CustomTargetingKeyValuesResponse(BaseModel):
    model_config = _config()
    key: str
    values: list[CustomTargetingKeyValue]


class AudienceSegmentsResponse(BaseModel):
    model_config = _config()
    audience_segments: list[AudienceSegment]


# ---------------------------------------------------------------------------
# Principals
# ---------------------------------------------------------------------------


class PrincipalCreate(BaseModel):
    """Idempotent on (tenant, external_id). Repeat POST returns the existing row.

    ``platform_mappings`` must declare at least one adapter mapping (e.g.
    ``{"google_ad_manager": {"advertiser_id": "12345"}}``). The model-layer
    validator enforces non-empty; the storefront knows the tenant's ad
    server context and supplies the right mapping.
    """

    model_config = _config()

    external_id: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=200)
    platform_mappings: dict = Field(..., min_length=1)
    agent_url: str | None = None
    brand_domain: str | None = None


class PrincipalUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    platform_mappings: dict | None = None
    agent_url: str | None = None
    brand_domain: str | None = None
    billing_enabled: bool | None = None


class PrincipalRead(BaseModel):
    model_config = _config()

    principal_id: str
    external_id: str | None
    name: str
    platform_mappings: dict
    agent_url: str | None
    brand_domain: str | None
    billing_enabled: bool
    created_at: datetime
    updated_at: datetime


class PrincipalCreated(PrincipalRead):
    """Returned on principal creation. Includes the access token; only emitted
    once — token is never returned again from list/get endpoints."""

    access_token: str


class PrincipalListResponse(BaseModel):
    model_config = _config()
    principals: list[PrincipalRead]


class TokenRotatedResponse(BaseModel):
    model_config = _config()
    principal_id: str
    access_token: str


# ---------------------------------------------------------------------------
# Dynamic product composition
# ---------------------------------------------------------------------------


class DynamicProductCreate(BaseModel):
    """Body for ``POST /api/v1/products``. Idempotency-Key header is required."""

    model_config = _config()

    inventory_profile_id: str = Field(..., min_length=1)
    custom_targeting_profile_ids: list[str] = Field(default_factory=list)
    adcp_targeting: dict | None = Field(
        default=None,
        description="Optional AdCP-native targeting baked into the product at compose time",
    )
    agreed_cpm: Decimal = Field(..., gt=Decimal("0"))
    flight_start: date
    flight_end: date
    principal_id: str = Field(..., min_length=1)
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
    custom_targeting_profile_ids: list[str]
    agreed_cpm: Decimal
    flight_start: date
    flight_end: date
    principal_id: str
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
    "unknown_custom_targeting_profile",
    "unknown_segment",
    "missing_principal",
    "expired_inventory_profile",
    "expired_custom_targeting_profile",
    "invalid_flight_dates",
]


class CompositionErrorDetail(BaseModel):
    """Single typed entry in a composition_invalid error response."""

    model_config = _config()

    code: _COMPOSITION_ERROR_CODES
    inventory_profile_id: str | None = None
    custom_targeting_profile_id: str | None = None
    segment_id: str | None = None
    dimension: str | None = None
    format_id: str | None = None
    channel: str | None = None
    principal_id: str | None = None
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
