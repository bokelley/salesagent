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
    BuyerAdvertiserMapping,
    CreateBuyerAdvertiserMappingRequest,
    GAMAdapterConfig,
    InitialPrincipalRequest,
    ListBuyerAdvertiserMappingsResponse,
    ListTenantsResponse,
    MockAdapterConfig,
    ProvisionedPrincipalResponse,
    ProvisionTenantRequest,
    ProvisionTenantResponse,
    TenantDetail,
    TenantSummary,
    UpdateBuyerAdvertiserMappingRequest,
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
        "house_domain": "acme.example.com",
        "public_agent_url": "https://agent.scope3.com/tenant_acme",
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


# ---------------------------------------------------------------------------
# Sprint 1.8 — buyer-advertiser routing rule schemas
# ---------------------------------------------------------------------------


def test_provision_request_accepts_default_gam_advertiser_id():
    """Sprint 1.8 — managed-mode provision can pass the fall-through advertiser inline."""
    req = ProvisionTenantRequest.model_validate(_provision_payload(default_gam_advertiser_id="12345"))
    assert req.default_gam_advertiser_id == "12345"


def test_provision_request_default_gam_advertiser_id_optional_at_provision():
    """Required-before-activation, not required-at-provision — covers the
    'create then attach default' flow."""
    req = ProvisionTenantRequest.model_validate(_provision_payload())
    assert req.default_gam_advertiser_id is None


def test_update_tenant_request_can_patch_default_gam_advertiser_id():
    req = UpdateTenantRequest.model_validate({"default_gam_advertiser_id": "67890"})
    assert req.default_gam_advertiser_id == "67890"


def test_update_tenant_request_rejects_blank_default_gam_advertiser_id():
    """``min_length=1`` blocks empty-string PATCH (no path to clear once set)."""
    with pytest.raises(ValidationError):
        UpdateTenantRequest.model_validate({"default_gam_advertiser_id": ""})


def test_tenant_detail_carries_default_gam_advertiser_id():
    detail = TenantDetail(
        tenant_id="t1",
        name="Test",
        managed_externally=True,
        is_active=True,
        billing_plan="standard",
        adapter_configured=True,
        created_at=datetime.now(),
        default_gam_advertiser_id="12345",
    )
    assert detail.default_gam_advertiser_id == "12345"


def test_buyer_advertiser_mapping_response_round_trip():
    payload = {
        "id": "rule_abc123",
        "operator_domain": "interchange.io",
        "brand_house": "coca-cola.com",
        "brand_id": "sprite",
        "gam_advertiser_id": "12345",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    mapping = BuyerAdvertiserMapping.model_validate(payload)
    assert mapping.brand_id == "sprite"


def test_buyer_advertiser_mapping_allows_null_brand_house_and_brand_id():
    """Operator-wildcard rule shape — both brand fields null."""
    mapping = BuyerAdvertiserMapping(
        id="rule_x",
        operator_domain="buyer.scope3.com",
        gam_advertiser_id="99",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    assert mapping.brand_house is None and mapping.brand_id is None


def test_create_mapping_request_minimum_fields():
    """Operator-wildcard create — operator_domain and gam_advertiser_id only."""
    req = CreateBuyerAdvertiserMappingRequest(operator_domain="buyer.scope3.com", gam_advertiser_id="99")
    assert req.brand_house is None and req.brand_id is None


def test_create_mapping_request_rejects_blank_operator_domain():
    with pytest.raises(ValidationError):
        CreateBuyerAdvertiserMappingRequest(operator_domain="", gam_advertiser_id="99")


def test_create_mapping_request_rejects_blank_gam_advertiser_id():
    with pytest.raises(ValidationError):
        CreateBuyerAdvertiserMappingRequest(operator_domain="x.example.com", gam_advertiser_id="")


def test_create_mapping_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        CreateBuyerAdvertiserMappingRequest.model_validate(
            {"operator_domain": "x.example.com", "gam_advertiser_id": "99", "rogue": "oops"}
        )


def test_update_mapping_request_does_not_expose_operator_domain():
    """Natural-key field is intentionally not patchable — DELETE+POST only."""
    fields = set(UpdateBuyerAdvertiserMappingRequest.model_fields.keys())
    assert "operator_domain" not in fields
    assert fields == {"brand_house", "brand_id", "gam_advertiser_id"}


def test_update_mapping_request_all_optional():
    req = UpdateBuyerAdvertiserMappingRequest()
    assert req.brand_house is None
    assert req.brand_id is None
    assert req.gam_advertiser_id is None


def test_update_mapping_request_rejects_blank_gam_advertiser_id():
    with pytest.raises(ValidationError):
        UpdateBuyerAdvertiserMappingRequest.model_validate({"gam_advertiser_id": ""})


def test_list_mappings_response_round_trip():
    payload = {
        "mappings": [
            {
                "id": "rule_abc",
                "operator_domain": "interchange.io",
                "brand_house": None,
                "brand_id": None,
                "gam_advertiser_id": "12345",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        ],
        "count": 1,
    }
    resp = ListBuyerAdvertiserMappingsResponse.model_validate(payload)
    assert resp.count == 1
    assert resp.mappings[0].operator_domain == "interchange.io"
