"""Unit tests for the Tenant Management API Pydantic schemas.

Each schema is exercised on its happy path and on each documented rejection
path (CLAUDE.md pattern #7: ``extra="forbid"`` is on in dev/CI).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from src.admin.api_schemas.tenant_management import (
    AdapterConfigResponse,
    AdapterStatusResponse,
    ApiError,
    GAMAdapterConfig,
    InitialPrincipalRequest,
    ListTenantsResponse,
    MockAdapterConfig,
    ProvisionedPrincipalResponse,
    ProvisionTenantRequest,
    ProvisionTenantResponse,
    TenantDetail,
    TenantSummary,
    UpdateTenantRequest,
)
from src.admin.api_schemas.tenant_management import (
    TestConnectionResponse as ConnectionTestResponse,
)

# ---------------------------------------------------------------------------
# Adapter configs
# ---------------------------------------------------------------------------


def _gam_payload(**overrides):
    base = {
        "type": "google_ad_manager",
        "network_code": "12345",
        "service_account_email": "sa@example.com",
        "service_account_key_json": '{"type":"service_account"}',
    }
    base.update(overrides)
    return base


def test_gam_adapter_config_happy_path():
    cfg = GAMAdapterConfig(**_gam_payload())
    assert cfg.network_code == "12345"
    assert cfg.service_account_key_json.get_secret_value() == '{"type":"service_account"}'
    assert cfg.refresh_token is None


def test_gam_adapter_config_rejects_blank_network_code():
    with pytest.raises(ValidationError):
        GAMAdapterConfig(**_gam_payload(network_code=""))


def test_gam_adapter_config_rejects_extra_field():
    with pytest.raises(ValidationError):
        GAMAdapterConfig(**_gam_payload(extra="oops"))


def test_mock_adapter_config_default_dry_run():
    cfg = MockAdapterConfig(type="mock")
    assert cfg.dry_run is False


def test_mock_adapter_config_rejects_extra_field():
    with pytest.raises(ValidationError):
        MockAdapterConfig(type="mock", network_code="123")


# ---------------------------------------------------------------------------
# ProvisionTenantRequest / Response
# ---------------------------------------------------------------------------


def _provision_payload(**overrides):
    base = {
        "name": "Acme News",
        "external_org_id": "org_123",
        "external_source": "scope3",
        "contact_email": "ops@acme.example.com",
        "adapter": _gam_payload(),
    }
    base.update(overrides)
    return base


def test_provision_request_happy_path_gam():
    req = ProvisionTenantRequest.model_validate(_provision_payload())
    assert isinstance(req.adapter, GAMAdapterConfig)
    assert req.default_currency == "USD"
    assert req.billing_plan == "standard"
    assert req.initial_principal is None


def test_provision_request_happy_path_mock():
    payload = _provision_payload(adapter={"type": "mock"})
    req = ProvisionTenantRequest.model_validate(payload)
    assert isinstance(req.adapter, MockAdapterConfig)


def test_provision_request_with_initial_principal():
    payload = _provision_payload(initial_principal={"name": "Default Advertiser"})
    req = ProvisionTenantRequest.model_validate(payload)
    assert req.initial_principal is not None
    assert req.initial_principal.name == "Default Advertiser"


def test_provision_request_missing_required_field_raises():
    payload = _provision_payload()
    payload.pop("external_org_id")
    with pytest.raises(ValidationError):
        ProvisionTenantRequest.model_validate(payload)


def test_provision_request_rejects_bad_currency_length():
    payload = _provision_payload(default_currency="DOLLARS")
    with pytest.raises(ValidationError):
        ProvisionTenantRequest.model_validate(payload)


def test_provision_request_rejects_bad_email():
    payload = _provision_payload(contact_email="not-an-email")
    with pytest.raises(ValidationError):
        ProvisionTenantRequest.model_validate(payload)


def test_provision_request_rejects_unknown_field():
    payload = _provision_payload(rogue_field="oops")
    with pytest.raises(ValidationError):
        ProvisionTenantRequest.model_validate(payload)


def test_provision_request_rejects_unknown_adapter_type():
    payload = _provision_payload(adapter={"type": "kevel", "network_id": "x"})
    with pytest.raises(ValidationError):
        ProvisionTenantRequest.model_validate(payload)


def test_initial_principal_request_rejects_blank_name():
    with pytest.raises(ValidationError):
        InitialPrincipalRequest(name="")


def test_provisioned_principal_response_has_no_token_field():
    # Sprint 1 contract: managed-mode principals do not carry per-principal API tokens.
    fields = set(ProvisionedPrincipalResponse.model_fields.keys())
    assert "api_token" not in fields
    assert fields == {"principal_id", "name"}


def test_provision_response_managed_externally_is_locked_to_true():
    payload = {
        "tenant_id": "tenant_x",
        "name": "Acme",
        "external_org_id": "org_x",
        "external_source": "scope3",
        "managed_externally": True,
        "created_at": datetime.now().isoformat(),
        "mcp_url": "/mcp/",
        "a2a_url": "/a2a",
        "admin_url_path": "/tenant/tenant_x",
        "adapter": AdapterStatusResponse(type="mock", configured=True, connection_test_passed=True).model_dump(),
    }
    resp = ProvisionTenantResponse.model_validate(payload)
    assert resp.managed_externally is True

    # Setting it to False must be rejected (Literal[True]).
    payload["managed_externally"] = False
    with pytest.raises(ValidationError):
        ProvisionTenantResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# Tenant lifecycle schemas
# ---------------------------------------------------------------------------


def test_tenant_summary_minimum_fields():
    summary = TenantSummary(
        tenant_id="t1",
        name="Test",
        managed_externally=False,
        is_active=True,
        billing_plan="standard",
        adapter_configured=False,
        created_at=datetime.now(),
    )
    assert summary.external_org_id is None


def test_list_tenants_response_round_trip():
    payload = {
        "tenants": [
            {
                "tenant_id": "t1",
                "name": "Test",
                "managed_externally": False,
                "is_active": True,
                "billing_plan": "standard",
                "adapter_configured": False,
                "created_at": datetime.now().isoformat(),
            }
        ],
        "count": 1,
    }
    resp = ListTenantsResponse.model_validate(payload)
    assert resp.count == 1


def test_tenant_detail_inherits_summary_fields():
    detail = TenantDetail(
        tenant_id="t1",
        name="Test",
        managed_externally=False,
        is_active=True,
        billing_plan="standard",
        adapter_configured=False,
        created_at=datetime.now(),
        contact_email="x@y.example.com",
        default_currency="USD",
    )
    assert detail.contact_email == "x@y.example.com"


def test_update_tenant_request_all_optional():
    req = UpdateTenantRequest()
    assert req.name is None and req.contact_email is None and req.billing_plan is None


def test_update_tenant_request_does_not_expose_external_org_id():
    fields = set(UpdateTenantRequest.model_fields.keys())
    assert "external_org_id" not in fields
    assert "external_source" not in fields
    assert "is_active" not in fields  # mutated via /deactivate /reactivate, not PATCH


def test_update_tenant_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        UpdateTenantRequest.model_validate({"is_active": False})


# ---------------------------------------------------------------------------
# AdapterConfigResponse / TestConnectionResponse / ApiError
# ---------------------------------------------------------------------------


def test_adapter_config_response_redacted():
    resp = AdapterConfigResponse(
        type="google_ad_manager",
        configured=True,
        network_code="123",
        service_account_email="sa@x.example.com",
        service_account_key_json="<encrypted>",
    )
    assert resp.service_account_key_json == "<encrypted>"


def test_connection_test_response_failure_includes_error():
    resp = ConnectionTestResponse(success=False, error="timeout", tested_at=datetime.now())
    assert resp.success is False and resp.error == "timeout"


def test_api_error_minimum():
    err = ApiError(error="x", message="y")
    assert err.details is None
