"""Integration test for custom targeting values endpoint.

This test validates that the /api/tenant/{id}/targeting/values/{key_id} endpoint
correctly queries GAM in real-time to fetch custom targeting values.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, GAMInventory, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _create_gam_targeting_values_tenant(factory_session, *, tenant_id: str, key_id: str) -> None:
    from tests.factories import AdapterConfigFactory, TenantFactory
    from tests.helpers.targeting_values import create_custom_targeting_key_row

    tenant = TenantFactory(
        tenant_id=tenant_id,
        name=f"Targeting Values {tenant_id}",
        subdomain=tenant_id.replace("_", "-"),
        ad_server="google_ad_manager",
    )
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token="test_refresh_token",
    )
    create_custom_targeting_key_row(tenant, key_id)
    factory_session.commit()


def test_get_targeting_values_endpoint(authenticated_admin_session, integration_db):
    """Test /api/tenant/{id}/targeting/values/{key_id} queries GAM and returns values."""
    # integration_db fixture sets up the database environment
    # Use get_db_session() to interact with it
    with get_db_session() as db_session:
        # Create test tenant with GAM configuration
        tenant = Tenant(tenant_id="test_tenant", name="Test Tenant", subdomain="test", ad_server="google_ad_manager")
        db_session.add(tenant)
        db_session.flush()

        # Create adapter config
        adapter_config = AdapterConfig(
            tenant_id="test_tenant",
            adapter_type="google_ad_manager",
            gam_network_code="123456",
            gam_refresh_token="test_refresh_token",
        )
        db_session.add(adapter_config)

        # Create custom targeting key (synced from GAM)
        key_id = "123456"
        key = GAMInventory(
            tenant_id="test_tenant",
            inventory_type="custom_targeting_key",
            inventory_id=key_id,
            name="sport",
            status="ACTIVE",
            inventory_metadata={
                "display_name": "Sport",
                "type": "PREDEFINED",
                "reportable_type": "ON",
            },
        )
        db_session.add(key)
        db_session.commit()

    # Mock GAM client to return values
    from src.adapters.gam_inventory_discovery import CustomTargetingValue

    mock_values = [
        CustomTargetingValue(
            id="val_001",
            custom_targeting_key_id=key_id,
            name="basketball",
            display_name="Basketball",
            match_type="EXACT",
            status="ACTIVE",
        ),
        CustomTargetingValue(
            id="val_002",
            custom_targeting_key_id=key_id,
            name="football",
            display_name="Football",
            match_type="EXACT",
            status="ACTIVE",
        ),
        CustomTargetingValue(
            id="val_003",
            custom_targeting_key_id=key_id,
            name="soccer",
            display_name="Soccer",
            match_type="BROAD",
            status="INACTIVE",
        ),
    ]

    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        with patch("googleads.ad_manager.AdManagerClient") as mock_ad_manager_client:
            with patch("googleads.oauth2.GoogleRefreshTokenClient") as mock_oauth_client:
                mock_gam_instance = MagicMock()
                mock_gam_instance.discover_custom_targeting_values_for_key.return_value = mock_values
                mock_gam_class.return_value = mock_gam_instance

                # Make request to API endpoint
                response = authenticated_admin_session.get(f"/api/tenant/test_tenant/targeting/values/{key_id}")

                # Validate OAuth client was created correctly
                mock_oauth_client.assert_called_once()
                oauth_call_kwargs = mock_oauth_client.call_args.kwargs
                assert oauth_call_kwargs["refresh_token"] == "test_refresh_token"

                # Validate AdManager client was created correctly
                mock_ad_manager_client.assert_called_once()
                ad_manager_call_args = mock_ad_manager_client.call_args
                assert ad_manager_call_args.kwargs["network_code"] == "123456"

                # Validate GAMInventoryDiscovery was instantiated with client and tenant_id
                mock_gam_class.assert_called_once()
                gam_call_kwargs = mock_gam_class.call_args.kwargs
                assert "client" in gam_call_kwargs
                assert gam_call_kwargs["tenant_id"] == "test_tenant"

                mock_gam_instance.discover_custom_targeting_values_for_key.assert_called_once_with(
                    key_id, max_values=1000
                )

    # Validate response
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.data}"

    data = response.json
    assert "values" in data
    assert "count" in data
    assert data["count"] == 3

    # Validate value structure
    values = data["values"]
    assert len(values) == 3

    # Check first value (basketball)
    basketball = next((v for v in values if v["name"] == "basketball"), None)
    assert basketball is not None
    assert basketball["id"] == "val_001"
    assert basketball["display_name"] == "Basketball"
    assert basketball["match_type"] == "EXACT"
    assert basketball["status"] == "ACTIVE"
    assert basketball["key_id"] == key_id
    assert basketball["key_name"] == "sport"

    # Check inactive value (soccer)
    soccer = next((v for v in values if v["name"] == "soccer"), None)
    assert soccer is not None
    assert soccer["status"] == "INACTIVE"
    assert soccer["match_type"] == "BROAD"


def test_get_targeting_values_empty_result(authenticated_admin_session, integration_db):
    """Test endpoint returns empty array when GAM returns no values for key."""
    with get_db_session() as db_session:
        # Create test tenant with GAM configuration
        tenant = Tenant(
            tenant_id="test_tenant_empty",
            name="Test Tenant Empty",
            subdomain="testempty",
            ad_server="google_ad_manager",
        )
        db_session.add(tenant)
        db_session.flush()

        # Create adapter config
        adapter_config = AdapterConfig(
            tenant_id="test_tenant_empty",
            adapter_type="google_ad_manager",
            gam_network_code="123456",
            gam_refresh_token="test_refresh_token",
        )
        db_session.add(adapter_config)

        # Create custom targeting key with no values
        key_id = "999999"
        key = GAMInventory(
            tenant_id="test_tenant_empty",
            inventory_type="custom_targeting_key",
            inventory_id=key_id,
            name="empty_key",
            status="ACTIVE",
            inventory_metadata={"display_name": "Empty Key", "type": "FREEFORM"},
        )
        db_session.add(key)
        db_session.commit()

    # Mock GAM client to return no values
    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        mock_gam_instance = MagicMock()
        mock_gam_instance.discover_custom_targeting_values_for_key.return_value = []
        mock_gam_class.return_value = mock_gam_instance

        # Make request to API endpoint
        response = authenticated_admin_session.get(f"/api/tenant/test_tenant_empty/targeting/values/{key_id}")

    # Validate response
    assert response.status_code == 200
    data = response.json
    assert data["count"] == 0
    assert data["values"] == []


def test_get_targeting_values_live_fetch_failure_returns_502(authenticated_admin_session, factory_session):
    """Cache-miss live GAM failures should be distinguishable from local server errors."""
    from src.admin.blueprints import inventory as inventory_blueprint

    inventory_blueprint._TARGETING_VALUES_LIVE_FETCH_RATE.clear()
    tenant_id = "live_fetch_failure_values"
    key_id = "17304127"
    _create_gam_targeting_values_tenant(factory_session, tenant_id=tenant_id, key_id=key_id)

    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        with patch("googleads.ad_manager.AdManagerClient"):
            with patch("googleads.oauth2.GoogleRefreshTokenClient"):
                mock_gam_instance = MagicMock()
                mock_gam_instance.discover_custom_targeting_values_for_key.side_effect = RuntimeError("GAM down")
                mock_gam_class.return_value = mock_gam_instance

                response = authenticated_admin_session.get(f"/api/tenant/{tenant_id}/targeting/values/{key_id}")

    assert response.status_code == 502
    assert response.json == {"error": "GAM unreachable, try again", "source": "live_failed"}


def test_get_targeting_values_refresh_is_rate_limited(authenticated_admin_session, factory_session):
    """Explicit refreshes should not allow unlimited live GAM calls for one tenant."""
    from src.admin.blueprints import inventory as inventory_blueprint

    inventory_blueprint._TARGETING_VALUES_LIVE_FETCH_RATE.clear()
    tenant_id = "refresh_rate_limited_values"
    key_id = "17304128"
    _create_gam_targeting_values_tenant(factory_session, tenant_id=tenant_id, key_id=key_id)

    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        with patch("googleads.ad_manager.AdManagerClient"):
            with patch("googleads.oauth2.GoogleRefreshTokenClient"):
                mock_gam_instance = MagicMock()
                mock_gam_instance.discover_custom_targeting_values_for_key.return_value = []
                mock_gam_class.return_value = mock_gam_instance

                responses = [
                    authenticated_admin_session.get(f"/api/tenant/{tenant_id}/targeting/values/{key_id}?refresh=1")
                    for _ in range(11)
                ]

    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429
    assert "retry_after_seconds" in responses[10].json
    assert mock_gam_instance.discover_custom_targeting_values_for_key.call_count == 10


def test_get_targeting_values_tenant_isolation(authenticated_admin_session, integration_db):
    """Test endpoint requires GAM configuration (validates tenant isolation at config level)."""
    with get_db_session() as db_session:
        # Create tenant without GAM configuration
        tenant = Tenant(tenant_id="tenant_a", name="Tenant A", subdomain="tenanta")
        db_session.add(tenant)

        # Create key
        key_id = "shared_key_123"
        key = GAMInventory(
            tenant_id="tenant_a",
            inventory_type="custom_targeting_key",
            inventory_id=key_id,
            name="category",
            status="ACTIVE",
            inventory_metadata={"display_name": "Category", "type": "PREDEFINED"},
        )
        db_session.add(key)
        db_session.commit()

    # Request values without GAM configured - should fail
    response = authenticated_admin_session.get(f"/api/tenant/tenant_a/targeting/values/{key_id}")

    assert response.status_code == 400
    data = response.json
    assert "error" in data
    # Updated to match new, more specific error message
    assert "No adapter configured" in data["error"]


def test_get_targeting_values_embedded_storefront_owned_uncached_without_gam_auth_returns_needs_sync(
    authenticated_admin_session, factory_session, monkeypatch
):
    """Storefront-owned embedded sync can defer missing GAM auth to the host refresh path."""
    from tests.factories import AdapterConfigFactory, TenantFactory
    from tests.helpers.targeting_values import create_custom_targeting_key_row

    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)
    factory_session.info["management_api_caller"] = True
    tenant = TenantFactory(
        tenant_id="embedded_no_auth_values",
        name="Embedded No Auth",
        subdomain="embedded-no-auth-values",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token=None,
    )
    key_id = "17304123"
    create_custom_targeting_key_row(tenant, key_id)
    factory_session.commit()

    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/targeting/values/{key_id}")

    mock_gam_class.assert_not_called()
    assert response.status_code == 200
    assert response.json == {"count": 0, "needs_sync": True, "source": "uncached", "values": []}


def test_get_targeting_values_embedded_synced_empty_returns_cache(
    authenticated_admin_session, factory_session, monkeypatch
):
    """A successful empty host refresh should not keep asking for sync."""
    from tests.factories import AdapterConfigFactory, TenantFactory
    from tests.helpers.targeting_values import create_custom_targeting_key_row

    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)
    factory_session.info["management_api_caller"] = True
    tenant = TenantFactory(
        tenant_id="embedded_synced_empty_values",
        name="Embedded Synced Empty",
        subdomain="embedded-synced-empty-values",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token=None,
    )
    key_id = "17304126"
    key_row = create_custom_targeting_key_row(tenant, key_id)
    key_row.inventory_metadata = {
        **(key_row.inventory_metadata or {}),
        "values_synced_empty": True,
        "values_last_synced_at": "2026-05-22T00:00:00+00:00",
    }
    factory_session.commit()

    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/targeting/values/{key_id}")

    mock_gam_class.assert_not_called()
    assert response.status_code == 200
    assert response.json == {"count": 0, "source": "cache", "values": []}


def test_get_targeting_values_embedded_publisher_owned_without_gam_auth_returns_400(
    authenticated_admin_session, factory_session, monkeypatch
):
    """Publisher-owned embedded sync should surface local GAM misconfiguration."""
    from tests.factories import AdapterConfigFactory, TenantFactory
    from tests.helpers.targeting_values import create_custom_targeting_key_row

    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"inventory_sync": "publisher"}')
    factory_session.info["management_api_caller"] = True
    tenant = TenantFactory(
        tenant_id="embedded_publisher_owned_no_auth_values",
        name="Embedded Publisher Owned No Auth",
        subdomain="embedded-publisher-owned-no-auth-values",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token=None,
    )
    key_id = "17304124"
    create_custom_targeting_key_row(tenant, key_id)
    factory_session.commit()

    response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/targeting/values/{key_id}")

    assert response.status_code == 400
    assert response.json == {"error": "GAM authentication not configured. Please connect to GAM in tenant settings."}


def test_get_targeting_values_embedded_missing_adapter_config_returns_400(
    authenticated_admin_session, factory_session, monkeypatch
):
    """Host refresh can populate values, but it cannot repair a missing adapter config."""
    from tests.factories import TenantFactory
    from tests.helpers.targeting_values import create_custom_targeting_key_row

    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)
    factory_session.info["management_api_caller"] = True
    tenant = TenantFactory(
        tenant_id="embedded_missing_adapter_values",
        name="Embedded Missing Adapter",
        subdomain="embedded-missing-adapter-values",
        ad_server="google_ad_manager",
        is_embedded=True,
    )
    key_id = "17304125"
    create_custom_targeting_key_row(tenant, key_id)
    factory_session.commit()

    response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/targeting/values/{key_id}")

    assert response.status_code == 400
    assert response.json == {"error": "No adapter configured for this tenant"}


def test_get_targeting_values_requires_auth(admin_client, integration_db):
    """Test endpoint requires authentication."""
    with get_db_session() as db_session:
        # Create test tenant and key
        tenant = Tenant(tenant_id="auth_test", name="Auth Test", subdomain="authtest")
        db_session.add(tenant)
        db_session.commit()

    key_id = "auth_key_123"

    # Attempt to access without authentication
    response = admin_client.get(f"/api/tenant/auth_test/targeting/values/{key_id}")

    # Should redirect to login or return 401/403
    assert response.status_code in [302, 401, 403]
