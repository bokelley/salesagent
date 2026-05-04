"""Pydantic schemas for the Tenant Management API.

See ``docs/design/managed-tenant-mode-sprint-1.md`` for the per-endpoint contract.
All schemas use the project-wide ``get_pydantic_extra_mode()`` helper so they
forbid unknown fields in dev/CI and ignore them in production (CLAUDE.md
pattern #7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from src.core.config import get_pydantic_extra_mode

_EXTRA_MODE = get_pydantic_extra_mode()


def _config() -> ConfigDict:
    """Return a fresh ConfigDict for each schema."""
    return ConfigDict(extra=_EXTRA_MODE)


# ---------------------------------------------------------------------------
# Adapter config — discriminated union (sprint 1 ships GAM + Mock)
# ---------------------------------------------------------------------------


class GAMAdapterConfig(BaseModel):
    """Google Ad Manager adapter configuration."""

    model_config = _config()

    type: Literal["google_ad_manager"] = "google_ad_manager"
    network_code: str = Field(..., min_length=1, max_length=32)
    service_account_email: str = Field(..., min_length=3, max_length=255)
    # Full JSON of the service account key, encrypted at rest by the model layer.
    service_account_key_json: SecretStr
    # Optional: present when authenticating via OAuth refresh token instead of an SA key.
    refresh_token: SecretStr | None = None


class MockAdapterConfig(BaseModel):
    """Mock adapter configuration (no real backend)."""

    model_config = _config()

    type: Literal["mock"] = "mock"
    # Mock adapter takes no real config; dry_run is a useful test hook.
    dry_run: bool = False


# Public discriminated alias used in request/response schemas.
AdapterConfig = Annotated[
    GAMAdapterConfig | MockAdapterConfig,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Provision: request + response
# ---------------------------------------------------------------------------


class InitialPrincipalRequest(BaseModel):
    """Optional initial advertiser created at provision time."""

    model_config = _config()

    name: str = Field(..., min_length=1, max_length=255)
    # GAM advertiser ID, etc. Optional — not all adapters use external IDs.
    external_advertiser_id: str | None = Field(default=None, max_length=255)


class ProvisionTenantRequest(BaseModel):
    """Request body for ``POST /tenants/provision``."""

    model_config = _config()

    # Identity
    name: str = Field(..., min_length=1, max_length=255)
    external_org_id: str = Field(..., min_length=1, max_length=255)
    external_source: str = Field(..., min_length=1, max_length=64)
    contact_email: EmailStr

    # Adapter config (required — a tenant without an adapter is useless)
    adapter: AdapterConfig

    # Defaults
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_plan: str = Field(default="standard", max_length=64)

    # Optional convenience: create one principal in the same call
    initial_principal: InitialPrincipalRequest | None = None


class ProvisionedPrincipalResponse(BaseModel):
    """Initial principal returned from provision.

    Note: managed-mode buyer-protocol auth flows through the identity-propagation
    contract, not per-principal tokens (see sprint 2 § Auth boundary). No
    ``api_token`` field is emitted here.
    """

    model_config = _config()

    principal_id: str
    name: str


class AdapterStatusResponse(BaseModel):
    """Compact adapter status block returned in provision responses."""

    model_config = _config()

    type: str
    configured: bool
    connection_test_passed: bool
    connection_test_error: str | None = None


class ProvisionTenantResponse(BaseModel):
    """Response body for ``POST /tenants/provision``."""

    model_config = _config()

    tenant_id: str
    name: str
    external_org_id: str
    external_source: str
    managed_externally: Literal[True] = True
    created_at: datetime

    # Surfaces — URLs the upstream platform needs to know about.
    mcp_url: str
    a2a_url: str
    admin_url_path: str

    adapter: AdapterStatusResponse

    initial_principal: ProvisionedPrincipalResponse | None = None


# ---------------------------------------------------------------------------
# Tenant lifecycle: list / get / patch
# ---------------------------------------------------------------------------


class TenantSummary(BaseModel):
    """Compact tenant entry for ``GET /tenants`` listings."""

    model_config = _config()

    tenant_id: str
    name: str
    # Subdomain is open-instance metadata; included so legacy callers can still pivot on it.
    subdomain: str | None = None
    external_org_id: str | None = None
    external_source: str | None = None
    managed_externally: bool
    is_active: bool
    billing_plan: str
    ad_server: str | None = None
    adapter_configured: bool
    created_at: datetime


class TenantDetail(TenantSummary):
    """Full tenant detail returned from ``GET /tenants/{id}`` and lifecycle responses."""

    model_config = _config()

    contact_email: EmailStr | None = None
    default_currency: str | None = None


class ListTenantsResponse(BaseModel):
    model_config = _config()

    tenants: list[TenantSummary]
    count: int


class UpdateTenantRequest(BaseModel):
    """PATCH body — only platform-managed fields are exposed.

    ``external_org_id`` and ``external_source`` are NOT modifiable post-creation;
    they identify the tenant's relationship with the upstream platform.
    ``is_active`` is changed via the deactivate/reactivate endpoints, not PATCH.
    """

    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=255)
    contact_email: EmailStr | None = None
    billing_plan: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Adapter management
# ---------------------------------------------------------------------------


class AdapterConfigResponse(BaseModel):
    """Adapter config view returned with secrets redacted."""

    model_config = _config()

    type: str
    configured: bool
    network_code: str | None = None
    service_account_email: str | None = None
    # Always redacted — clients should not see secret material on read paths.
    service_account_key_json: str | None = None
    refresh_token: str | None = None


class TestConnectionResponse(BaseModel):
    """Result of an adapter connection probe."""

    model_config = _config()

    success: bool
    error: str | None = None
    tested_at: datetime


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApiError(BaseModel):
    """Standard problem-detail shape used by every Tenant Management endpoint."""

    model_config = _config()

    error: str
    message: str
    details: dict[str, Any] | None = None
