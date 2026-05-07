"""Triton TAP adapter configuration schemas.

Connection schema holds publisher-scoped credentials (one publisher account
maps to many stations). Product schema holds the inventory selection — station
IDs, station groups, genres, stream types, and dayparts that flights target.

Connection-level ``password`` is encrypted at rest with the same Fernet key
the rest of the codebase uses for sensitive credentials. Encryption happens
transparently via Pydantic field serializer/validator: callers see plaintext
in memory, persisted JSON contains ciphertext.

``auth_type`` lets us swap between Triton's user-login flow (the documented
default) and an OAuth2 ``client_credentials`` machine flow if Triton's Account
Team issues service-account credentials. The same ``username``/``password``
slots carry either credential pair — the client posts the right body shape
based on ``auth_type``.
"""

from typing import Literal

from pydantic import Field, field_serializer, field_validator

from src.adapters.base import BaseConnectionConfig, BaseProductConfig
from src.core.utils.encryption import decrypt_api_key, encrypt_api_key, is_encrypted


def _require_https(url: str, field_name: str) -> str:
    """Reject non-https URLs to prevent credential exfiltration to attacker hosts.

    A tenant admin who can edit connection config must not be able to point the
    auth flow at ``http://attacker.example`` and have us POST publisher
    credentials there.
    """
    if not url.startswith("https://"):
        raise ValueError(f"{field_name} must be an https:// URL — got {url!r}")
    return url


class TritonConnectionConfig(BaseConnectionConfig):
    """Publisher-level credentials for the Triton TAP Media Buying API.

    A publisher account owns many stations; the credentials are publisher-scoped,
    not per-station. Station selection is a flight-level targeting concern and
    lives in :class:`TritonProductConfig`.
    """

    auth_type: Literal["password", "oauth_client_credentials"] = Field(
        default="password",
        description=(
            "Authentication flow. 'password' posts username+password to the Login API "
            "(documented default). 'oauth_client_credentials' posts grant_type/client_id/"
            "client_secret — used when Triton issues service-account credentials. The same "
            "username/password slots hold the credential pair for either flow."
        ),
        json_schema_extra={"ui_order": 0, "enum": ["password", "oauth_client_credentials"]},
    )
    username: str = Field(
        ...,
        description="Triton publisher login email (or OAuth client_id when auth_type=oauth_client_credentials)",
        json_schema_extra={"ui_order": 1},
    )
    password: str = Field(
        ...,
        description="Triton publisher password (or OAuth client_secret when auth_type=oauth_client_credentials)",
        json_schema_extra={"secret": True, "ui_order": 2},
    )
    base_url: str = Field(
        default="https://mbapi.tritondigital.com",
        description="TAP Media Buying API base URL",
        json_schema_extra={"ui_order": 3},
    )
    login_url: str = Field(
        default="https://login.tritondigital.com",
        description="TAP Login API base URL (issues JWTs)",
        json_schema_extra={"ui_order": 4},
    )
    default_advertiser_id: str | None = Field(
        default=None,
        description="Fallback TAP advertiser ID for principals without explicit triton mappings",
        json_schema_extra={"ui_order": 5},
    )

    @field_serializer("password")
    def _encrypt_password(self, value: str) -> str:
        return value if is_encrypted(value) else encrypt_api_key(value)

    @field_validator("password", mode="after")
    @classmethod
    def _decrypt_password(cls, value: str) -> str:
        return decrypt_api_key(value) if is_encrypted(value) else value

    @field_validator("base_url", "login_url", mode="after")
    @classmethod
    def _enforce_https(cls, value: str, info) -> str:
        return _require_https(value, info.field_name)


class TritonProductConfig(BaseProductConfig):
    """Per-product TAP inventory selection and pacing.

    Station IDs and station-group IDs are translated into TAP flight
    ``targetingRules`` of the form
    ``{type:"in", dimension:"station"|"station-group", values:[...]}``.
    """

    station_ids: list[str] = Field(
        default_factory=list,
        description="TAP station IDs this product targets",
    )
    station_group_ids: list[str] = Field(
        default_factory=list,
        description="TAP station-group IDs (publisher-defined station bundles)",
    )
    genres: list[str] = Field(
        default_factory=list,
        description="TAP station genres (e.g. 'Rock', 'News', 'Sports')",
    )
    stream_types: list[str] = Field(
        default_factory=list,
        description="Stream types: 'radio_stream', 'podcast'",
    )
    daypart_ids: list[str] = Field(
        default_factory=list,
        description="TAP daypart entity IDs",
    )
