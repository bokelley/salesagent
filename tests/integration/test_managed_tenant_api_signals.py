"""Integration tests for embedded signal-mapping authoring APIs."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetSignalsRequest
from src.core.tools.signals import _get_signals_impl
from tests.factories import (
    AdapterConfigFactory,
    GAMInventoryFactory,
    MediaBuyFactory,
    PrincipalFactory,
    SpringServeInventoryFactory,
    TenantFactory,
)
from tests.helpers.managed_tenant_api import (
    bind_factories_to_session,
    configure_google_ad_manager_adapter,
    make_management_api_test_client,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-managed-tenant-signals-test-key"


@pytest.fixture
def management_api_client(integration_db):
    return make_management_api_test_client(API_KEY)


@pytest.fixture
def bound_factories(integration_db):
    with bind_factories_to_session() as session:
        session.info["management_api_caller"] = True
        yield session


@pytest.fixture
def gam_tenant(bound_factories):
    tenant = TenantFactory(
        tenant_id="tenant_signals_gam",
        name="Wonderstruck",
        subdomain="wonderstruck-signals",
        ad_server="google_ad_manager",
        is_embedded=True,
        public_agent_url="https://interchange.io",
    )
    configure_google_ad_manager_adapter(tenant)
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="audience_segment",
        inventory_id="seg_auto_intenders",
        name="Auto Intenders",
        path=["Audiences", "Auto Intenders"],
        inventory_metadata={"type": "FIRST_PARTY"},
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_key",
        inventory_id="key_interest",
        name="Interest",
        path=["Custom Targeting", "Interest"],
        inventory_metadata={"type": "PREDEFINED"},
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_value",
        inventory_id="val_sports",
        name="Sports",
        path=["Custom Targeting", "Interest", "Sports"],
        inventory_metadata={"custom_targeting_key_id": "key_interest"},
    )
    return tenant


def _signal_payload(**overrides):
    payload = {
        "signal_id": "audience_auto_intenders",
        "name": "Auto Intenders",
        "description": "First-party auto audience.",
        "value_type": "binary",
        "tags": ["audience", "first_party"],
        "adapter_config": {
            "type": "passthrough",
            "kind": "audience_segment",
            "segment_id": "seg_auto_intenders",
        },
        "data_provider": "publisher_1p",
        "targeting_dimension": "audience",
    }
    payload.update(overrides)
    return payload


def test_signal_capabilities_and_candidates_surface_adapter_mapping_templates(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    capabilities = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/adapter-capabilities",
        headers=auth_headers,
    )
    assert capabilities.status_code == 200, capabilities.get_data(as_text=True)
    mapping_kinds = {mapping["mapping_kind"] for mapping in capabilities.get_json()["mapping_kinds"]}
    assert {"audience_segment", "custom_key_value", "gam_targeting_groups"} <= mapping_kinds

    audience_candidates = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/candidates"
        "?candidate_type=audience_segment&q=Auto",
        headers=auth_headers,
    )
    assert audience_candidates.status_code == 200, audience_candidates.get_data(as_text=True)
    audience = audience_candidates.get_json()["candidates"][0]
    assert audience["mapping_kind"] == "audience_segment"
    assert audience["adapter_config_template"]["segment_id"] == "seg_auto_intenders"
    assert audience["default_signal"]["signal_id"] == "audience_auto_intenders"

    value_candidates = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/candidates"
        "?candidate_type=custom_targeting_value&parent_id=key_interest&q=Sports",
        headers=auth_headers,
    )
    assert value_candidates.status_code == 200, value_candidates.get_data(as_text=True)
    value = value_candidates.get_json()["candidates"][0]
    assert value["mapping_kind"] == "custom_key_value"
    assert value["adapter_config_template"] == {
        "type": "passthrough",
        "kind": "custom_key_value",
        "key_id": "key_interest",
        "value_id": "val_sports",
    }


def test_signal_candidates_support_camel_case_params_and_lazy_refresh_values(management_api_client, bound_factories):
    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_signals_gam_lazy_values",
        name="Lazy Values",
        subdomain="lazy-values-signals",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    configure_google_ad_manager_adapter(tenant)
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_key",
        inventory_id="key_lazy_interest",
        name="Interest",
        path=["Custom Targeting", "Interest"],
        inventory_metadata={"type": "PREDEFINED"},
    )
    bound_factories.commit()

    from src.adapters.gam_inventory_discovery import CustomTargetingValue

    discovery = MagicMock()
    discovery.discover_custom_targeting_values_for_key.return_value = [
        CustomTargetingValue(
            id="val_lazy_sports",
            custom_targeting_key_id="key_lazy_interest",
            name="Sports",
            display_name="Sports",
            match_type="EXACT",
            status="ACTIVE",
        )
    ]

    with patch("src.admin.tenant_management_api.build_gam_inventory_discovery", return_value=discovery):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/signals/candidates"
            "?candidateType=custom_targeting_value&parentId=key_lazy_interest&q=Sports",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["count"] == 1
    assert body["candidates"][0]["external_id"] == "val_lazy_sports"
    assert body["candidates"][0]["adapter_config_template"] == {
        "type": "passthrough",
        "kind": "custom_key_value",
        "key_id": "key_lazy_interest",
        "value_id": "val_lazy_sports",
    }
    discovery.discover_custom_targeting_values_for_key.assert_called_once_with("key_lazy_interest", max_values=1000)


def test_signal_value_candidates_query_miss_does_not_refresh_cached_values(management_api_client, bound_factories):
    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_signals_gam_cached_query_miss",
        name="Cached Query Miss",
        subdomain="cached-query-miss-signals",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    configure_google_ad_manager_adapter(tenant)
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_key",
        inventory_id="key_cached_interest",
        name="Interest",
        path=["Custom Targeting", "Interest"],
        inventory_metadata={"type": "PREDEFINED"},
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_value",
        inventory_id="val_cached_sports",
        name="Sports",
        path=["Custom Targeting", "Interest", "Sports"],
        inventory_metadata={"custom_targeting_key_id": "key_cached_interest"},
    )
    bound_factories.commit()

    with patch("src.admin.tenant_management_api.build_gam_inventory_discovery") as build_discovery:
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/signals/candidates"
            "?candidate_type=custom_targeting_value&parent_id=key_cached_interest&q=Travel",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["count"] == 0
    build_discovery.assert_not_called()


def test_signal_value_candidates_synced_empty_key_does_not_refresh(management_api_client, bound_factories):
    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_signals_gam_synced_empty",
        name="Synced Empty",
        subdomain="synced-empty-signals",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    configure_google_ad_manager_adapter(tenant)
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_key",
        inventory_id="key_empty_interest",
        name="Interest",
        path=["Custom Targeting", "Interest"],
        inventory_metadata={
            "type": "PREDEFINED",
            "values_synced_empty": True,
            "values_last_synced_at": "2026-05-22T00:00:00+00:00",
        },
    )
    bound_factories.commit()

    with patch("src.admin.tenant_management_api.build_gam_inventory_discovery") as build_discovery:
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/signals/candidates"
            "?candidate_type=custom_targeting_value&parent_id=key_empty_interest&q=Sports",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["count"] == 0
    build_discovery.assert_not_called()


def test_signal_key_candidates_ignore_null_path_parts(management_api_client, gam_tenant, bound_factories):
    client, auth_headers = management_api_client
    GAMInventoryFactory(
        tenant=gam_tenant,
        inventory_type="custom_targeting_key",
        inventory_id="key_null_path",
        name="Null Path Key",
        path=[None, "Null Path Key"],
        inventory_metadata={"type": "PREDEFINED"},
    )
    bound_factories.commit()

    response = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/candidates"
        "?candidate_type=custom_targeting_key&q=Null",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["count"] == 1
    assert body["candidates"][0]["external_id"] == "key_null_path"
    assert body["candidates"][0]["path"] == ["Null Path Key"]


def test_signal_mapping_crud_round_trips_execution_config_and_buyer_discovery(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    create = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals",
        json=_signal_payload(),
        headers=auth_headers,
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    created = create.get_json()
    assert created["adapter_config"]["segment_id"] == "seg_auto_intenders"
    assert created["etag"]

    list_response = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals",
        headers=auth_headers,
    )
    assert list_response.status_code == 200, list_response.get_data(as_text=True)
    assert list_response.get_json()["signals"][0]["signal_id"] == "audience_auto_intenders"

    buyer_response = asyncio.run(
        _get_signals_impl(
            GetSignalsRequest(),
            identity=ResolvedIdentity(
                tenant_id=gam_tenant.tenant_id,
                tenant={
                    "tenant_id": gam_tenant.tenant_id,
                    "ad_server": "google_ad_manager",
                    "public_agent_url": "https://interchange.io",
                },
                principal_id="buyer_agent",
                protocol="rest",
            ),
        )
    )
    assert "audience_auto_intenders" in {signal.signal_id.id for signal in buyer_response.signals}

    update = client.put(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/audience_auto_intenders",
        json=_signal_payload(name="Auto Intenders Updated"),
        headers=auth_headers,
    )
    assert update.status_code == 200, update.get_data(as_text=True)
    assert update.get_json()["name"] == "Auto Intenders Updated"

    delete = client.delete(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/audience_auto_intenders",
        headers=auth_headers,
    )
    assert delete.status_code == 200, delete.get_data(as_text=True)
    assert delete.get_json()["success"] is True


def test_signal_validation_checks_cached_adapter_candidates(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    response = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals:validate",
        json=_signal_payload(
            signal_id="audience_missing",
            adapter_config={"type": "passthrough", "kind": "audience_segment", "segment_id": "missing_segment"},
        ),
        headers=auth_headers,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["valid"] is False
    assert body["issues"][0]["code"] == "signal_mapping_candidate_not_found"
    assert body["issues"][0]["field"] == "adapter_config.segment_id"


def test_signal_delete_blocks_active_media_buy_references(management_api_client, gam_tenant, bound_factories):
    client, auth_headers = management_api_client
    principal = PrincipalFactory(tenant=gam_tenant)
    MediaBuyFactory(
        tenant=gam_tenant,
        principal=principal,
        raw_request={
            "packages": [
                {
                    "package_id": "pkg_001",
                    "product_id": "homepage_takeover",
                    "targeting_overlay": {"audience_include": ["audience_auto_intenders"]},
                }
            ]
        },
    )
    bound_factories.commit()

    create = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals",
        json=_signal_payload(),
        headers=auth_headers,
    )
    assert create.status_code == 201, create.get_data(as_text=True)

    blocked = client.delete(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/audience_auto_intenders",
        headers=auth_headers,
    )
    assert blocked.status_code == 409, blocked.get_data(as_text=True)
    assert blocked.get_json()["error"] == "signal_mapping_in_use"

    forced = client.delete(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/signals/audience_auto_intenders"
        "?confirm_referenced=true",
        headers=auth_headers,
    )
    assert forced.status_code == 200, forced.get_data(as_text=True)


def test_springserve_value_list_signal_candidate_and_create(management_api_client, bound_factories):
    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_signals_springserve",
        name="CafeMedia",
        subdomain="cafemedia-signals",
        ad_server="springserve",
        is_embedded=True,
    )
    AdapterConfigFactory(tenant=tenant, adapter_type="springserve")
    SpringServeInventoryFactory(
        tenant=tenant,
        entity_type="key",
        entity_id="700",
        name="Audience",
        key_id=None,
        raw_json={"id": "700", "name": "Audience"},
    )
    SpringServeInventoryFactory(
        tenant=tenant,
        entity_type="value_list",
        entity_id="9001",
        name="Travel Intenders",
        key_id="700",
        raw_json={"id": "9001", "name": "Travel Intenders", "key_id": "700", "key_name": "Audience"},
    )
    bound_factories.commit()

    candidates = client.get(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/signals/candidates"
        "?candidate_type=value_list&parent_id=700&q=Travel",
        headers=auth_headers,
    )
    assert candidates.status_code == 200, candidates.get_data(as_text=True)
    candidate = candidates.get_json()["candidates"][0]
    assert candidate["mapping_kind"] == "springserve_value_list"
    assert candidate["adapter_config_template"] == {
        "type": "passthrough",
        "kind": "springserve_value_list",
        "key_id": "700",
        "key_name": "Audience",
        "value_list_id": "9001",
    }

    create = client.post(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/signals",
        json={
            **candidate["default_signal"],
            "signal_id": "audience_travel_intenders",
            "description": "SpringServe value-list audience.",
            "tags": ["audience"],
            "data_provider": "publisher_1p",
        },
        headers=auth_headers,
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    assert create.get_json()["adapter_config"]["value_list_id"] == "9001"
