"""Integration tests: Media Buy Creation with Inventory Profiles.

Tests that verify products referencing inventory profiles work correctly in
the media buy creation flow. Uses the mock adapter (which does not natively
support inventory profiles) to verify the pipeline doesn't break.

Requires PostgreSQL (integration_db).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    InventoryProfile,
    PricingOption,
    Principal,
)
from src.core.database.repositories import ProductRepository
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.factories import CurrencyLimitFactory, InventoryProfileFactory, PrincipalFactory, TenantFactory
from tests.factories.spec_required_kwargs import required_request_kwargs
from tests.helpers.adcp_factories import create_test_db_product, create_test_package_request


def _make_context(tenant_id: str, principal_id: str) -> ResolvedIdentity:
    """Create a ResolvedIdentity for testing."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id},
        testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
        protocol="mcp",
    )


def _get_future_date_range() -> tuple[datetime, datetime]:
    """Return a future date range with timezone info."""
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=7)
    return start, end


@pytest.mark.requires_db
async def test_create_media_buy_with_inventory_profile_as_wholesale_product(factory_session):
    """Inventory bundles are wholesale products even without Product rows."""
    tenant = TenantFactory()
    principal = PrincipalFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        principal_id="test_principal_bundle_product",
        platform_mappings={"mock": {"id": "test_advertiser"}},
    )
    CurrencyLimitFactory(tenant=tenant, tenant_id=tenant.tenant_id, currency_code="EUR")
    profile = InventoryProfileFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        profile_id="test_bundle_product_media_buy",
        name="Bundle Product for Media Buy",
        forecast={"impressions": 100000},
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        publisher_properties=[
            {
                "publisher_domain": "example.com",
                "property_tags": ["all_inventory"],
                "selection_type": "by_tag",
            }
        ],
    )

    assert ProductRepository(factory_session, tenant.tenant_id).get_by_id(profile.profile_id) is None

    start_time, end_time = _get_future_date_range()
    ctx = _make_context(tenant.tenant_id, principal.principal_id)
    req = CreateMediaBuyRequest(
        **required_request_kwargs(),
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id=profile.profile_id,
                pricing_option_id="cpm_usd_auction",
                bid_price=1.0,
                budget=150.0,
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    response, task_status = await _create_media_buy_impl(req=req, identity=ctx)

    assert task_status == "completed"
    assert response.media_buy_id is not None
    assert response.packages is not None
    assert [package.product_id for package in response.packages] == [profile.profile_id]
    assert ProductRepository(factory_session, tenant.tenant_id).get_by_id(profile.profile_id) is None


@pytest.mark.requires_db
def test_gam_adapter_accepts_inventory_profile_as_wholesale_product(factory_session):
    """GAM product-config lookup resolves buyer-visible bundle projections."""
    tenant = TenantFactory(ad_server="google_ad_manager")
    principal = PrincipalFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        principal_id="test_principal_gam_bundle_product",
        platform_mappings={"google_ad_manager": {"advertiser_id": "123456"}},
    )
    profile = InventoryProfileFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        profile_id="test_gam_bundle_product_media_buy",
        name="GAM Bundle Product for Media Buy",
        inventory_config={
            "adapter": "google_ad_manager",
            "placements": ["pl_bundle"],
            "include_descendants": True,
            "selectors": [{"selector_type": "placement", "external_id": "pl_bundle"}],
        },
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        publisher_properties=[
            {
                "publisher_domain": "example.com",
                "property_tags": ["all_inventory"],
                "selection_type": "by_tag",
            }
        ],
    )
    factory_session.commit()
    assert ProductRepository(factory_session, tenant.tenant_id).get_by_id(profile.profile_id) is None

    package = MediaPackage(
        package_id="pkg_test_gam_bundle_product_media_buy",
        name=profile.name,
        delivery_type="non_guaranteed",
        impressions=1000,
        format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")],
        product_id=profile.profile_id,
        budget=100.0,
    )
    request = CreateMediaBuyRequest(
        **required_request_kwargs(),
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id=profile.profile_id,
                pricing_option_id="cpm_usd_auction",
                bid_price=1.0,
                budget=100.0,
            )
        ],
        start_time=_get_future_date_range()[0],
        end_time=_get_future_date_range()[1],
    )

    adapter = GoogleAdManager(
        config={"manual_approval_required": True, "manual_approval_operations": ["create_media_buy"]},
        principal=principal,
        network_code="123456",
        advertiser_id="123456",
        trafficker_id="654321",
        tenant_id=tenant.tenant_id,
        dry_run=True,
    )
    with patch.object(adapter.workflow_manager, "create_manual_order_workflow_step", return_value="step_bundle"):
        response = adapter.create_media_buy(
            request=request,
            packages=[package],
            start_time=request.start_time,
            end_time=request.end_time,
            package_pricing_info={
                package.package_id: {
                    "pricing_model": "cpm",
                    "currency": "USD",
                    "is_fixed": False,
                    "rate": None,
                    "bid_price": 1.0,
                }
            },
        )

    assert getattr(response, "media_buy_id", None) is not None
    assert getattr(response, "errors", None) is None
    assert getattr(response, "packages", None)
    assert response.packages[0].package_id == package.package_id
    assert ProductRepository(factory_session, tenant.tenant_id).get_by_id(profile.profile_id) is None


@pytest.mark.requires_db
async def test_create_media_buy_with_profile_based_product(sample_tenant):
    """Test that media buy creation succeeds when product references an inventory profile."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_media_buy",
            name="Test Profile for Media Buy",
            description="Profile with specific ad units",
            inventory_config={
                "ad_units": ["12345", "67890"],
                "placements": ["99999"],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["example_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_media_buy",
            name="Profile-Based Product",
            description="Product using inventory profile",
            inventory_profile_id=profile.id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_media_buy",
            name="Test Advertiser",
            access_token="test_token_media_buy",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        req = CreateMediaBuyRequest(
            **required_request_kwargs(),
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id=product.product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
            ],
            start_time=start_time,
            end_time=end_time,
        )
        response, task_status = await _create_media_buy_impl(req=req, identity=ctx)

        # Verify success
        assert not hasattr(response, "errors") or response.errors is None or response.errors == [], (
            f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown'}"
        )
        assert response.media_buy_id is not None
        assert response.packages is not None
        assert len(response.packages) >= 1


@pytest.mark.requires_db
async def test_create_media_buy_with_profile_formats(sample_tenant):
    """Test that media buy creation handles profile-based format validation."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_format_validation",
            name="Test Profile for Format Validation",
            description="Profile with specific formats",
            inventory_config={
                "ad_units": ["12345"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
                {"agent_url": "https://test.example.com", "id": "display_728x90"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["example_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_format_validation",
            name="Format Validation Product",
            description="Product using inventory profile",
            inventory_profile_id=profile.id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_format_validation",
            name="Test Advertiser Format",
            access_token="test_token_format",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        # Create media buy - should succeed or return structured error, not crash
        try:
            req = CreateMediaBuyRequest(
                **required_request_kwargs(),
                brand={"domain": "testbrand.com"},
                packages=[
                    create_test_package_request(
                        product_id=product.product_id,
                        pricing_option_id="cpm_usd_fixed",
                        budget=150.0,
                    )
                ],
                start_time=start_time,
                end_time=end_time,
            )
            response, _ = await _create_media_buy_impl(req=req, identity=ctx)
            # Either succeeds or returns structured error - both are valid
            assert response is not None
        except ValueError:
            # Validation error is also acceptable behavior
            pass


@pytest.mark.requires_db
async def test_multiple_products_same_profile_in_media_buy(sample_tenant):
    """Test media buy with multiple products referencing the same profile."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_multiple",
            name="Shared Profile",
            description="Profile shared by multiple products",
            inventory_config={
                "ad_units": ["shared_unit_1", "shared_unit_2"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["prop_shared"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        products = []
        for i in range(3):
            product = create_test_db_product(
                tenant_id=sample_tenant["tenant_id"],
                product_id=f"test_product_shared_{i}",
                name=f"Shared Profile Product {i}",
                description=f"Product {i} sharing profile",
                inventory_profile_id=profile.id,
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)

            pricing = PricingOption(
                tenant_id=sample_tenant["tenant_id"],
                product_id=product.product_id,
                pricing_model="cpm",
                rate=Decimal("15.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)
            products.append(product)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_shared",
            name="Test Advertiser Shared",
            access_token="test_token_shared",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        # Use only the first product (AdCP spec: package has singular product_id)
        req = CreateMediaBuyRequest(
            **required_request_kwargs(),
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id=products[i].product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
                for i in range(3)
            ],
            start_time=start_time,
            end_time=end_time,
        )
        response, _ = await _create_media_buy_impl(req=req, identity=ctx)

        assert not hasattr(response, "errors") or response.errors is None or response.errors == [], (
            f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown'}"
        )
        assert response.media_buy_id is not None
        assert response.packages is not None
        assert len(response.packages) == 3


@pytest.mark.requires_db
async def test_media_buy_reflects_profile_updates(sample_tenant):
    """Test that media buy uses current profile config, not stale data."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_updates",
            name="Updatable Profile",
            description="Profile that will be updated",
            inventory_config={
                "ad_units": ["old_unit"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "old.example.com",
                    "property_ids": ["old_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        profile_id = profile.id

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_updates",
            name="Product with Updatable Profile",
            description="Product using updatable profile",
            inventory_profile_id=profile_id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_updates",
            name="Test Advertiser Updates",
            access_token="test_token_updates",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        # Update profile AFTER product creation
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.inventory_config = {
            "ad_units": ["new_unit"],
            "placements": ["new_placement"],
            "include_descendants": True,
        }
        profile.publisher_properties = [
            {
                "selection_type": "by_id",
                "publisher_domain": "new.example.com",
                "property_ids": ["new_property"],
            }
        ]
        session.commit()

        # Create media buy AFTER profile update — should still succeed
        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        req = CreateMediaBuyRequest(
            **required_request_kwargs(),
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id=product.product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
            ],
            start_time=start_time,
            end_time=end_time,
        )
        response, _ = await _create_media_buy_impl(req=req, identity=ctx)

        assert not hasattr(response, "errors") or response.errors is None or response.errors == [], (
            f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown'}"
        )
        assert response.media_buy_id is not None
