"""Integration tests for embedded wholesale-product authoring APIs."""

from __future__ import annotations

import pytest
from flask import Flask

from src.admin.tenant_management_api import tenant_management_api
from tests.factories import (
    AdapterConfigFactory,
    AuthorizedPropertyFactory,
    GAMInventoryFactory,
    PublisherPartnerFactory,
    TenantFactory,
)
from tests.helpers.managed_tenant_api import bind_factories_to_session, install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-managed-tenant-wholesale-test-key"


@pytest.fixture
def management_api_client(integration_db):
    api_key = install_management_api_key(API_KEY)
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application.test_client(), {"X-Tenant-Management-API-Key": api_key}


@pytest.fixture
def bound_factories(integration_db):
    with bind_factories_to_session() as session:
        session.info["management_api_caller"] = True
        yield session


@pytest.fixture
def gam_tenant(bound_factories):
    tenant = TenantFactory(
        tenant_id="tenant_wholesale_gam",
        name="Wonderstruck",
        subdomain="wonderstruck",
        ad_server="google_ad_manager",
        is_embedded=True,
        public_agent_url="https://interchange.io",
    )
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="12345",
        gam_service_account_email="sa@example.com",
        gam_auth_method="service_account",
        gam_service_account_json_plaintext='{"type":"service_account"}',
    )
    PublisherPartnerFactory(
        tenant=tenant,
        publisher_domain="wonderstruck.com",
        display_name="Wonderstruck",
        is_verified=True,
        sync_status="success",
    )
    AuthorizedPropertyFactory(
        tenant=tenant,
        property_id="wonderstruck_site",
        publisher_domain="wonderstruck.com",
        name="Wonderstruck Site",
        tags=["premium", "all_inventory"],
        verification_status="verified",
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="ad_unit",
        inventory_id="au_home",
        name="Homepage Ad Unit",
        path=["Wonderstruck", "Homepage"],
        inventory_metadata={"parent_id": None, "has_children": True, "sizes": [{"width": 970, "height": 250}]},
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="placement",
        inventory_id="pl_homepage_takeover",
        name="Homepage Takeover Placement",
        path=["Wonderstruck", "Homepage Takeover"],
        inventory_metadata={"parent_id": None, "targeted_ad_unit_ids": ["au_home"]},
    )
    return tenant


def _wholesale_payload(**overrides):
    payload = {
        "wholesale_product_id": "homepage_takeover",
        "name": "Homepage Takeover",
        "description": "High-impact homepage package.",
        "status": "active",
        "delivery_type": "guaranteed",
        "channels": ["display"],
        "pricing_options": [
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": True,
                "rate": "40.00",
            }
        ],
        "forecast": {"impressions": 1000000},
        "inventory": {
            "publisher_properties": [
                {
                    "publisher_domain": "wonderstruck.com",
                    "selection_type": "by_id",
                    "property_ids": ["wonderstruck_site"],
                }
            ],
            "creative_formats": [
                {
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": "homepage_takeover",
                    },
                    "slot_requirements": [
                        {
                            "slot_id": "leaderboard",
                            "name": "Leaderboard",
                            "asset_type": "image",
                            "width": 970,
                            "height": 250,
                            "required": True,
                        }
                    ],
                }
            ],
            "execution": {
                "adapter": "google_ad_manager",
                "selectors": [
                    {
                        "selector_type": "placement",
                        "external_id": "pl_homepage_takeover",
                    },
                    {
                        "selector_type": "ad_unit",
                        "external_id": "au_home",
                        "options": {"include_descendants": True},
                    },
                ],
                "format_bindings": [
                    {
                        "format_id": {
                            "agent_url": "https://creative.adcontextprotocol.org",
                            "id": "homepage_takeover",
                        },
                        "adapter_config": {
                            "creative_placeholders": [{"slot_id": "leaderboard", "size": "970x250"}],
                            "roadblocking": "as_many_as_possible",
                        },
                    }
                ],
            },
        },
        "targeting_capabilities": {"allowed_dimensions": ["geo", "device"]},
        "optimization_capabilities": {"allowed_goals": ["impressions"]},
    }
    payload.update(overrides)
    return payload


def test_inventory_discovery_surfaces_adapter_selectors_and_publisher_properties(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    capabilities = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/inventory/adapter-capabilities",
        headers=auth_headers,
    )
    assert capabilities.status_code == 200, capabilities.get_data(as_text=True)
    selector_types = {selector["selector_type"] for selector in capabilities.get_json()["selector_types"]}
    assert {"ad_unit", "placement"} <= selector_types

    selectors = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/inventory/selectors"
        "?selector_type=ad_unit&q=Homepage",
        headers=auth_headers,
    )
    assert selectors.status_code == 200, selectors.get_data(as_text=True)
    assert selectors.get_json()["selectors"][0]["external_id"] == "au_home"

    properties = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/inventory/publisher-properties",
        headers=auth_headers,
    )
    assert properties.status_code == 200, properties.get_data(as_text=True)
    body = properties.get_json()
    assert body["domains"][0]["publisher_domain"] == "wonderstruck.com"
    assert body["properties"][0]["property_id"] == "wonderstruck_site"
    assert {selector["selection_type"] for selector in body["allowed_selectors"]} == {"all", "by_id", "by_tag"}


def test_wholesale_product_crud_persists_product_inventory_and_pricing(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()

    validation = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
        headers=auth_headers,
        json=payload,
    )
    assert validation.status_code == 200, validation.get_data(as_text=True)
    assert validation.get_json()["valid"] is True

    preview = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:preview",
        headers=auth_headers,
        json=payload,
    )
    assert preview.status_code == 200, preview.get_data(as_text=True)
    assert preview.get_json()["adapter_projection"]["inventory_config"]["ad_units"] == ["au_home"]

    created = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    created_body = created.get_json()
    assert created_body["product_id"] == "homepage_takeover"
    assert created_body["inventory_profile_id"] == "homepage_takeover"
    assert created_body["pricing_options"][0]["rate"] == "40.00"
    assert created_body["inventory"]["execution"]["selectors"][0]["selector_type"] == "placement"

    listing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
    )
    assert listing.status_code == 200, listing.get_data(as_text=True)
    assert listing.get_json()["count"] == 1

    updated_payload = _wholesale_payload(
        name="Homepage Takeover Updated",
        status="draft",
        pricing_options=[
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": True,
                "rate": "45.00",
            }
        ],
    )
    updated = client.put(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
        json=updated_payload,
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)
    updated_body = updated.get_json()
    assert updated_body["name"] == "Homepage Takeover Updated"
    assert updated_body["status"] == "draft"
    assert updated_body["pricing_options"][0]["rate"] == "45.00"

    detail = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.get_data(as_text=True)
    assert detail.get_json()["inventory"]["creative_formats"][0]["slot_requirements"][0]["slot_id"] == "leaderboard"

    deleted = client.delete(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert deleted.status_code == 200, deleted.get_data(as_text=True)

    missing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert missing.status_code == 404
