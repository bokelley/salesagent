"""FreeWheel adapter configuration schemas.

Connection schema supports two auth flows, in priority order:

  1. **OAuth2 password grant (canonical)** — ``username`` + ``password``. The
     client mints a bearer at ``POST /auth/token`` on first use, caches it
     with TTL tracking, and auto-refreshes on 401 or expiry. This is what
     production users want — set credentials once, forget about rotation.

  2. **Pre-minted bearer token (escape hatch)** — ``api_token``. Used when a
     partner provisions a token for us out-of-band (e.g. publisher mints one
     on our behalf), or for testing without managing real credentials. No
     auto-refresh — when the 7-day TTL expires, rotate manually.

Exactly one of (username+password) OR api_token must be set. Both ``password``
and ``api_token`` are encrypted at rest with Fernet — same pattern as
``TritonConnectionConfig.password``.
"""

from typing import Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from src.adapters.base import BaseConnectionConfig, BaseProductConfig
from src.core.utils.encryption import decrypt_api_key, encrypt_api_key, is_encrypted

# Environment -> API host mapping. Tokens are environment-scoped — staging
# tokens won't work in prod and vice versa.
FREEWHEEL_HOSTS = {
    "production": "https://api.freewheel.tv",
    "staging": "https://api.stg.freewheel.tv",
}


class FreeWheelConnectionConfig(BaseConnectionConfig):
    """OAuth2-password-grant or pre-minted-bearer config for the FreeWheel
    Publisher API. Exactly one of (username + password) or api_token is
    required."""

    username: str | None = Field(
        default=None,
        description="FreeWheel User ID for OAuth2 password-grant authentication",
        json_schema_extra={"ui_order": 1},
    )
    password: str | None = Field(
        default=None,
        description="FreeWheel password — used to mint bearer tokens via /auth/token",
        json_schema_extra={"secret": True, "ui_order": 2},
    )
    api_token: str | None = Field(
        default=None,
        description=(
            "Pre-minted bearer token (advanced/testing). When set, "
            "username+password are ignored. Token has a ~7-day TTL and "
            "must be rotated manually."
        ),
        json_schema_extra={"secret": True, "ui_order": 3},
    )
    environment: Literal["production", "staging"] = Field(
        default="production",
        description="Which FreeWheel environment to target",
        json_schema_extra={"ui_order": 4, "enum": ["production", "staging"]},
    )
    default_advertiser_id: str | None = Field(
        default=None,
        description="Fallback FreeWheel advertiser ID for principals without explicit freewheel mappings",
        json_schema_extra={"ui_order": 5},
    )

    @property
    def base_url(self) -> str:
        return FREEWHEEL_HOSTS[self.environment]

    @field_serializer("password")
    def _encrypt_password(self, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return value if is_encrypted(value) else encrypt_api_key(value)

    @field_validator("password", mode="after")
    @classmethod
    def _decrypt_password(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return decrypt_api_key(value) if is_encrypted(value) else value

    @field_serializer("api_token")
    def _encrypt_token(self, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return value if is_encrypted(value) else encrypt_api_key(value)

    @field_validator("api_token", mode="after")
    @classmethod
    def _decrypt_token(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return decrypt_api_key(value) if is_encrypted(value) else value

    @model_validator(mode="after")
    def _require_credentials(self) -> "FreeWheelConnectionConfig":
        """Require either (username + password) or api_token."""
        has_password_grant = bool(self.username) and bool(self.password)
        has_token = bool(self.api_token)
        if not has_password_grant and not has_token:
            raise ValueError("FreeWheel config requires either (username + password) or api_token")
        return self


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
