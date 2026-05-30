"""Composition API e2e setup path.

These tests exercise the server-to-server authoring endpoints that let an
embedded host seed inventory bundles, signals, and wholesale products before a
buyer runs AdCP discovery.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, call

import pytest
from adcp import GetProductsRequest
from adcp import GetProductsResponse as LibraryGetProductsResponse

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetSignalsRequest
from src.core.tools.products import _get_products_impl
from src.core.tools.signals import _get_signals_impl

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("TENANT_MANAGEMENT_API_KEY", "composition-api-test-key")
    return {"X-Tenant-Management-API-Key": "composition-api-test-key"}


def _seed_tenant(tenant_id: str) -> None:
    from tests.factories import AuthorizedPropertyFactory, TenantFactory

    tenant = TenantFactory(
        tenant_id=tenant_id,
        subdomain=tenant_id.replace("_", "-"),
        ad_server="mock",
        brand_manifest_policy="public",
    )
    AuthorizedPropertyFactory(
        tenant=tenant,
        property_id="publisher_example_sports",
        publisher_domain="publisher.example.com",
        tags=["sports", "all_inventory"],
    )


def _seed_embedded_mock_tenant(factory_session, *, tenant_id: str, subdomain: str):
    from tests.factories import TenantFactory

    factory_session.info["management_api_caller"] = True
    tenant = TenantFactory(
        tenant_id=tenant_id,
        subdomain=subdomain,
        ad_server="mock",
        is_embedded=True,
        brand_manifest_policy="public",
    )
    factory_session.commit()
    factory_session.info.pop("management_api_caller", None)
    return tenant


def _identity(tenant_id: str) -> ResolvedIdentity:
    return ResolvedIdentity(
        tenant_id=tenant_id,
        principal_id=None,
        tenant={
            "tenant_id": tenant_id,
            "ad_server": "mock",
            "public_agent_url": f"https://{tenant_id}.example.com/agent",
            "brand_manifest_policy": "public",
            "advertising_policy": {"enabled": False},
        },
        principal=None,
        auth_method="api_key",
        raw_credential=None,
    )


def _inventory_profile_payload(profile_id: str) -> dict:
    return {
        "profile_id": profile_id,
        "name": "Homepage + Sports Bundle",
        "description": "Display inventory used for wholesale discovery tests.",
        "inventory_config": {
            "ad_units": ["au_home", "au_sports"],
            "placements": ["pl_top"],
            "include_descendants": True,
        },
        "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        "publisher_properties": [
            {
                "publisher_domain": "publisher.example.com",
                "property_tags": ["sports"],
                "selection_type": "by_tag",
            }
        ],
        "constraints": {
            "formats": ["display_300x250"],
            "channels": ["display"],
            "targeting_dimensions": ["audience"],
        },
    }


def _signal_payload(signal_id: str) -> dict:
    return {
        "signal_id": signal_id,
        "name": "Sports Fans",
        "description": "Publisher-declared sports audience.",
        "value_type": "binary",
        "categories": [],
        "adapter_config": {"kind": "audience_segment", "segment_id": "98765"},
        "data_provider": "publisher_1p",
        "targeting_dimension": "audience",
    }


def _product_payload(product_id: str, profile_id: str) -> dict:
    return {
        "product_id": product_id,
        "name": "Wholesale Sports Display",
        "description": "Profile-backed non-guaranteed wholesale product.",
        "inventory_profile_id": profile_id,
        "delivery_type": "non_guaranteed",
        "channels": ["display"],
        "countries": ["US"],
        "signal_targeting_allowed": True,
        "pricing_options": [
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": False,
                "price_guidance": {"floor": 3.0, "p50": 4.0, "p75": 5.0},
            }
        ],
    }


def test_authoring_inventory_signal_and_product_unblocks_buyer_discovery(admin_client, factory_session, monkeypatch):
    tenant_id = "composition_api_e2e"
    profile_id = "homepage_sports"
    signal_id = "sports_fans"
    product_id = "wholesale_sports_display"
    _seed_tenant(tenant_id)
    headers = _headers(monkeypatch)

    profile_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=_inventory_profile_payload(profile_id),
        headers=headers,
    )
    assert profile_resp.status_code == 201
    assert profile_resp.get_json()["profile_id"] == profile_id

    signal_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/signals",
        json=_signal_payload(signal_id),
        headers=headers,
    )
    assert signal_resp.status_code == 201
    assert signal_resp.get_json()["signal_id"] == signal_id
    assert "adapter_config" not in signal_resp.get_json()

    product_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=_product_payload(product_id, profile_id),
        headers=headers,
    )
    assert product_resp.status_code == 201
    product_body = product_resp.get_json()
    assert product_body["product_id"] == product_id
    assert product_body["inventory_profile_id"] == profile_id
    assert product_body["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"

    products = asyncio.run(
        _get_products_impl(
            GetProductsRequest(
                buying_mode="brief",
                brief="wholesale sports display inventory",
                brand={"domain": "buyer.example"},
            ),
            identity=_identity(tenant_id),
        )
    )
    assert [product.product_id for product in products.products] == [product_id]
    discovered_product = products.products[0].model_dump(mode="json")
    assert discovered_product["format_ids"][0]["id"] == "display_300x250"
    assert discovered_product["publisher_properties"][0]["property_tags"] == ["sports"]

    signals = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=_identity(tenant_id)))
    discovered_signal_ids = {signal.signal_agent_segment_id for signal in signals.signals}
    assert signal_id in discovered_signal_ids


def test_composition_product_routes_emit_tenant_and_protocol_catalog_notifications(
    admin_client,
    factory_session,
    monkeypatch,
):
    tenant_id = "composition_api_product_notifications"
    profile_id = "notification_profile"
    product_id = "notification_product"
    _seed_tenant(tenant_id)
    headers = _headers(monkeypatch)

    profile_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=_inventory_profile_payload(profile_id),
        headers=headers,
    )
    assert profile_resp.status_code == 201

    emit_event = Mock()
    notify_product = Mock()
    monkeypatch.setattr("src.admin.services.catalog_webhook_events.emit_event", emit_event)
    monkeypatch.setattr("src.admin.services.catalog_webhook_events.notify_product_catalog_changed", notify_product)

    create_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=_product_payload(product_id, profile_id),
        headers=headers,
    )
    assert create_resp.status_code == 201

    update_payload = {
        "name": "Renamed Notification Product",
        "pricing_options": [
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": False,
                "price_guidance": {"floor": 4.0, "p50": 5.0, "p75": 6.0},
            }
        ],
    }
    update_resp = admin_client.put(
        f"/api/v1/tenants/{tenant_id}/products/{product_id}",
        json=update_payload,
        headers=headers,
    )
    assert update_resp.status_code == 200

    delete_resp = admin_client.delete(
        f"/api/v1/tenants/{tenant_id}/products/{product_id}",
        headers=headers,
    )
    assert delete_resp.status_code == 204

    emitted_event_types = [args[1] for args, _kwargs in emit_event.call_args_list]
    assert emitted_event_types == [
        "product.created",
        "wholesale_feed.bulk_change",
        "product.updated",
        "product.priced",
        "wholesale_feed.bulk_change",
        "product.removed",
        "wholesale_feed.bulk_change",
    ]
    notify_product.assert_has_calls(
        [
            call(
                tenant_id=tenant_id,
                action="created",
                product_id=product_id,
                data={"name": "Wholesale Sports Display"},
                principal_ids=None,
            ),
            call(
                tenant_id=tenant_id,
                action="updated",
                product_id=product_id,
                data={"name": "Renamed Notification Product"},
                principal_ids=None,
            ),
            call(
                tenant_id=tenant_id,
                action="deleted",
                product_id=product_id,
                data={"name": "Renamed Notification Product"},
                principal_ids=None,
            ),
        ]
    )


def test_inventory_profile_create_infers_publisher_property_selection_type(
    admin_client,
    factory_session,
    monkeypatch,
):
    tenant_id = "composition_api_infer_property_selection"
    profile_id = "infer_property_selection"
    _seed_tenant(tenant_id)
    payload = _inventory_profile_payload(profile_id)
    payload["publisher_properties"] = [
        {
            "publisher_domain": "publisher.example.com",
            "property_ids": ["publisher_example_sports"],
        }
    ]

    response = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=payload,
        headers=_headers(monkeypatch),
    )

    assert response.status_code == 201
    from src.core.database.repositories.inventory_profile import InventoryProfileRepository

    factory_session.expire_all()
    profile = InventoryProfileRepository(factory_session, tenant_id).get_by_id(profile_id)
    assert profile is not None
    assert profile.publisher_properties == [
        {
            "publisher_domain": "publisher.example.com",
            "selection_type": "by_id",
            "property_ids": ["publisher_example_sports"],
        }
    ]


def test_inventory_profile_create_self_heals_local_example_authorization(
    admin_client,
    factory_session,
    monkeypatch,
):
    from src.core.database.repositories.tenant_config import TenantConfigRepository

    tenant = _seed_embedded_mock_tenant(
        factory_session,
        tenant_id="composition_api_local_example",
        subdomain="composition-api-local-example",
    )
    config_repo = TenantConfigRepository(factory_session, tenant.tenant_id)
    assert config_repo.get_authorized_property_by_id("example_com") is None

    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ADCP_TESTING", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    payload = _inventory_profile_payload("local_example_profile")
    payload["publisher_properties"] = [{"publisher_domain": "example.com", "selection_type": "all"}]

    response = admin_client.post(
        f"/api/v1/tenants/{tenant.tenant_id}/inventory-profiles",
        json=payload,
        headers=_headers(monkeypatch),
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    assert response.get_json()["profile_id"] == "local_example_profile"
    factory_session.expire_all()
    authorized_property = config_repo.get_authorized_property_by_id("example_com")
    assert authorized_property is not None
    assert authorized_property.publisher_domain == "example.com"
    partner = config_repo.get_publisher_partner_by_domain("example.com")
    assert partner is not None
    assert partner.is_verified is True


def test_inventory_profile_update_self_heals_local_example_authorization(
    admin_client,
    factory_session,
    monkeypatch,
):
    from src.core.database.repositories.tenant_config import TenantConfigRepository

    tenant = _seed_embedded_mock_tenant(
        factory_session,
        tenant_id="composition_api_update_local_example",
        subdomain="composition-api-update-local-example",
    )
    config_repo = TenantConfigRepository(factory_session, tenant.tenant_id)
    headers = _headers(monkeypatch)

    create_payload = _inventory_profile_payload("update_local_example_profile")
    create_payload["publisher_properties"] = []
    create_response = admin_client.post(
        f"/api/v1/tenants/{tenant.tenant_id}/inventory-profiles",
        json=create_payload,
        headers=headers,
    )
    assert create_response.status_code == 201, create_response.get_data(as_text=True)
    assert config_repo.get_authorized_property_by_id("example_com") is None

    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ADCP_TESTING", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    update_response = admin_client.put(
        f"/api/v1/tenants/{tenant.tenant_id}/inventory-profiles/update_local_example_profile",
        json={"publisher_properties": [{"publisher_domain": "example.com", "selection_type": "all"}]},
        headers=headers,
    )

    assert update_response.status_code == 200, update_response.get_data(as_text=True)
    factory_session.expire_all()
    authorized_property = config_repo.get_authorized_property_by_id("example_com")
    assert authorized_property is not None
    assert authorized_property.publisher_domain == "example.com"


def test_inventory_profile_create_rejects_unauthorized_publisher_property_selector(
    admin_client,
    factory_session,
    monkeypatch,
):
    tenant_id = "composition_api_unauthorized_publisher_property"
    profile_id = "unauthorized_property_selector"
    _seed_tenant(tenant_id)
    payload = _inventory_profile_payload(profile_id)
    payload["publisher_properties"] = [
        {
            "publisher_domain": "publisher.example.com",
            "selection_type": "by_id",
            "property_ids": ["not_authorized"],
        }
    ]

    response = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=payload,
        headers=_headers(monkeypatch),
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "invalid_publisher_properties"
    assert body["details"]["issues"][0]["code"] == "publisher_property_not_authorized"


def test_inventory_profile_create_rejects_unsupported_publisher_property_fields(
    admin_client,
    factory_session,
    monkeypatch,
):
    tenant_id = "composition_api_bad_publisher_property"
    profile_id = "bad_property_shape"
    _seed_tenant(tenant_id)
    payload = _inventory_profile_payload(profile_id)
    payload["publisher_properties"] = [
        {
            "publisher_domain": "publisher.example.com",
            "selection_type": "all",
            "name": "publisher.example.com",
            "property_type": "website",
        }
    ]

    response = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=payload,
        headers=_headers(monkeypatch),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_wholesale_discovery_retains_profile_backed_product_pricing(admin_client, factory_session, monkeypatch):
    """Wholesale discovery retains Composition API product pricing.

    Covers: UC-001-ALT-ANONYMOUS-DISCOVERY-05A
    """
    tenant_id = "composition_api_wholesale_pricing"
    profile_id = "homepage_sports"
    product_id = "wholesale_priced_display"
    _seed_tenant(tenant_id)
    headers = _headers(monkeypatch)

    profile_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=_inventory_profile_payload(profile_id),
        headers=headers,
    )
    assert profile_resp.status_code == 201

    product_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=_product_payload(product_id, profile_id),
        headers=headers,
    )
    assert product_resp.status_code == 201
    assert product_resp.get_json()["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"

    products = asyncio.run(
        _get_products_impl(
            GetProductsRequest(buying_mode="wholesale"),
            identity=_identity(tenant_id),
        )
    )

    assert [product.product_id for product in products.products] == [product_id]
    discovered_product = products.products[0].model_dump(mode="json")
    assert len(discovered_product["pricing_options"]) == 1
    pricing = discovered_product["pricing_options"][0]
    assert pricing["pricing_model"] == "cpm"
    assert pricing["currency"] == "USD"
    assert pricing["pricing_option_id"] == "cpm_usd_auction"
    assert pricing["floor_price"] == 3.0
    assert pricing["price_guidance"]["p50"] == 4.0
    assert pricing["price_guidance"]["p75"] == 5.0
    LibraryGetProductsResponse.model_validate(products.model_dump(mode="json"))


def test_brief_discovery_suppresses_profile_backed_product_pricing(admin_client, factory_session, monkeypatch):
    """Brief discovery still suppresses anonymous pricing.

    Covers: UC-001-ALT-ANONYMOUS-DISCOVERY-05
    """
    tenant_id = "composition_api_brief_pricing"
    profile_id = "homepage_sports"
    product_id = "brief_priced_display"
    _seed_tenant(tenant_id)
    headers = _headers(monkeypatch)

    profile_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=_inventory_profile_payload(profile_id),
        headers=headers,
    )
    assert profile_resp.status_code == 201

    product_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=_product_payload(product_id, profile_id),
        headers=headers,
    )
    assert product_resp.status_code == 201

    products = asyncio.run(
        _get_products_impl(
            GetProductsRequest(
                buying_mode="brief",
                brief="wholesale sports display inventory",
                brand={"domain": "buyer.example"},
            ),
            identity=_identity(tenant_id),
        )
    )

    assert [product.product_id for product in products.products] == [product_id]
    assert products.products[0].model_dump(mode="json")["pricing_options"] == []


def test_product_update_replaces_pricing_without_empty_option_gap(admin_client, factory_session, monkeypatch):
    tenant_id = "composition_api_update_pricing"
    profile_id = "homepage_sports"
    product_id = "wholesale_update_display"
    _seed_tenant(tenant_id)
    headers = _headers(monkeypatch)

    profile_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=_inventory_profile_payload(profile_id),
        headers=headers,
    )
    assert profile_resp.status_code == 201

    create_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=_product_payload(product_id, profile_id),
        headers=headers,
    )
    assert create_resp.status_code == 201

    update_payload = {
        "pricing_options": [
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": True,
                "rate": 6.25,
            }
        ]
    }
    update_resp = admin_client.put(
        f"/api/v1/tenants/{tenant_id}/products/{product_id}",
        json=update_payload,
        headers=headers,
    )

    assert update_resp.status_code == 200
    pricing = update_resp.get_json()["pricing_options"]
    assert len(pricing) == 1
    assert pricing[0]["pricing_option_id"] == "cpm_usd_fixed"
    assert pricing[0]["rate"] == "6.25"


def test_product_create_rejects_non_cpm_pricing_model(admin_client, factory_session, monkeypatch):
    tenant_id = "composition_api_bad_pricing"
    profile_id = "homepage_sports"
    _seed_tenant(tenant_id)
    headers = _headers(monkeypatch)

    profile_resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/inventory-profiles",
        json=_inventory_profile_payload(profile_id),
        headers=headers,
    )
    assert profile_resp.status_code == 201

    payload = _product_payload("bad_pricing_product", profile_id)
    payload["pricing_options"][0]["pricing_model"] = "cpp"
    resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=payload,
        headers=headers,
    )

    assert resp.status_code == 400


def test_product_create_requires_existing_inventory_profile(admin_client, factory_session, monkeypatch):
    tenant_id = "composition_api_missing_profile"
    _seed_tenant(tenant_id)

    resp = admin_client.post(
        f"/api/v1/tenants/{tenant_id}/products",
        json=_product_payload("orphan_product", "missing_profile"),
        headers=_headers(monkeypatch),
    )

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "inventory_profile_not_found"
