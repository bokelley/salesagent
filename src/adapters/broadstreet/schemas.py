"""Broadstreet adapter configuration schemas.

Defines connection and product configuration for the admin UI schema system.
``api_key`` is encrypted at rest with Fernet via Pydantic field
serializer/validator — same pattern as ``TritonConnectionConfig.password`` and
``FreeWheelConnectionConfig.client_secret``. Pre-encryption rows persisted before
this change continue to load (the validator passes plaintext through) and get
encrypted automatically on the next save.
"""

from pydantic import Field, field_serializer, field_validator

from src.adapters.base import BaseConnectionConfig, BaseProductConfig
from src.core.utils.encryption import decrypt_api_key, encrypt_api_key, is_encrypted


class BroadstreetConnectionConfig(BaseConnectionConfig):
    """Connection configuration for Broadstreet API.

    Stored in AdapterConfig.config_json at the tenant level.
    """

    network_id: str = Field(
        ...,
        description="Broadstreet network ID",
        json_schema_extra={"ui_order": 1},
    )
    api_key: str = Field(
        ...,
        description="Broadstreet API access token",
        json_schema_extra={"secret": True, "ui_order": 2},
    )
    default_advertiser_id: str | None = Field(
        default=None,
        description="Default advertiser ID for principals without platform_mappings",
        json_schema_extra={"ui_order": 3},
    )

    @field_serializer("api_key")
    def _encrypt_api_key(self, value: str) -> str:
        return value if is_encrypted(value) else encrypt_api_key(value)

    @field_validator("api_key", mode="after")
    @classmethod
    def _decrypt_api_key(cls, value: str) -> str:
        return decrypt_api_key(value) if is_encrypted(value) else value


class BroadstreetProductConfig(BaseProductConfig):
    """Product-level configuration for Broadstreet.

    Stored in Product.implementation_config. Controls how campaigns
    are created in Broadstreet when a media buy is executed.
    """

    # Zone targeting (core Broadstreet concept)
    targeted_zone_ids: list[str] = Field(
        default_factory=list,
        description="Broadstreet zone IDs to target",
        json_schema_extra={"ui_component": "zone_selector"},
    )

    # Pricing
    cost_type: str = Field(
        default="CPM",
        description="Pricing model: CPM or FLAT_RATE",
        json_schema_extra={"enum": ["CPM", "FLAT_RATE"]},
    )

    # Delivery settings
    delivery_rate: str = Field(
        default="EVEN",
        description="Delivery pacing",
        json_schema_extra={"enum": ["EVEN", "FRONTLOADED", "ASAP"]},
    )
    frequency_cap: int | None = Field(
        default=None,
        ge=1,
        description="Max impressions per user per day",
    )

    # Ad format settings
    ad_format: str = Field(
        default="display",
        description="Primary ad format",
        json_schema_extra={"enum": ["display", "html", "text"]},
    )
    allow_html_creatives: bool = Field(
        default=True,
        description="Allow HTML/JavaScript creatives",
    )
    allow_text_creatives: bool = Field(
        default=True,
        description="Allow text-only creatives",
    )
