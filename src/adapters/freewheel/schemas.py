"""FreeWheel adapter configuration schemas.

Connection schema holds the long-lived bearer token plus the target
environment (production or staging). The token is encrypted at rest with
Fernet via Pydantic field serializer/validator — identical pattern to
``TritonConnectionConfig.password``.

Authentication note: FreeWheel issues opaque bearer tokens with a ~7-day
TTL. There is no public refresh flow — when a token expires, the publisher
mints a new one and updates this config. ``token_info`` on the live client
surfaces remaining TTL so admin UIs can warn before expiry.

Product schema holds inventory-side selection: which FreeWheel placements
this product targets, and an optional pre-built targeting profile ID.
"""

from typing import Literal

from pydantic import Field, field_serializer, field_validator

from src.adapters.base import BaseConnectionConfig, BaseProductConfig
from src.core.utils.encryption import decrypt_api_key, encrypt_api_key, is_encrypted

# Environment -> API host mapping. Tokens are environment-scoped — staging
# tokens won't work in prod and vice versa.
FREEWHEEL_HOSTS = {
    "production": "https://api.freewheel.tv",
    "staging": "https://api.stg.freewheel.tv",
}


class FreeWheelConnectionConfig(BaseConnectionConfig):
    """Bearer-token configuration for the FreeWheel Publisher API.

    Tokens are issued by FreeWheel (or by a publisher minting one on a
    partner's behalf). 7-day TTL, no refresh flow — rotate by updating
    ``api_token`` when expiry approaches.
    """

    api_token: str = Field(
        ...,
        description="FreeWheel bearer token (issued by FreeWheel or the publisher)",
        json_schema_extra={"secret": True, "ui_order": 1},
    )
    environment: Literal["production", "staging"] = Field(
        default="production",
        description="Which FreeWheel environment to target",
        json_schema_extra={"ui_order": 2, "enum": ["production", "staging"]},
    )
    default_advertiser_id: str | None = Field(
        default=None,
        description="Fallback FreeWheel advertiser ID for principals without explicit freewheel mappings",
        json_schema_extra={"ui_order": 3},
    )

    @property
    def base_url(self) -> str:
        return FREEWHEEL_HOSTS[self.environment]

    @field_serializer("api_token")
    def _encrypt_token(self, value: str) -> str:
        return value if is_encrypted(value) else encrypt_api_key(value)

    @field_validator("api_token", mode="after")
    @classmethod
    def _decrypt_token(cls, value: str) -> str:
        return decrypt_api_key(value) if is_encrypted(value) else value


class FreeWheelProductConfig(BaseProductConfig):
    """Per-product FreeWheel inventory + targeting selection.

    Placements are FreeWheel's inventory primitive — each product points at
    one or more placements that line items will deliver into.
    """

    placement_ids: list[str] = Field(
        default_factory=list,
        description="FreeWheel placement IDs this product targets",
    )
    targeting_profile_id: str | None = Field(
        default=None,
        description="Optional pre-built FreeWheel targeting profile ID",
    )
    priority: int | None = Field(
        default=None,
        description="Line item priority (FreeWheel uses numeric priorities; lower = higher priority)",
    )
    custom_targeting: dict[str, list[str]] = Field(
        default_factory=dict,
        description="FreeWheel custom key-value targeting (e.g. {'genre': ['sports','news']})",
    )
