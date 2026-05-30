"""Sandbox advertiser/account no-spend behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.sandbox import INTERCHANGE_SANDBOX_ZERO_RATE_CARD
from src.core.schemas import CreateMediaBuySuccess
from src.core.schemas import Package as ResponsePackage
from tests.factories import (
    AccountFactory,
    AdapterConfigFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PublisherPartnerFactory,
    TenantAuthConfigFactory,
    TenantFactory,
)
from tests.factories.spec_required_kwargs import required_request_kwargs
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _future(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _pricing_inner(option):
    return option.root if hasattr(option, "root") else option


def _seed_priced_product(tenant):
    product = ProductFactory(
        tenant=tenant,
        product_id="sandbox_product",
        name="Sandbox Product",
        delivery_type="guaranteed",
    )
    PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("12.50"), is_fixed=True)
    PricingOptionFactory(
        product=product,
        pricing_model="cpm",
        rate=Decimal("4.00"),
        is_fixed=False,
        price_guidance={"floor": 4.0, "p50": 6.0, "p75": 8.0, "p90": 10.0},
    )
    return product


@pytest.mark.asyncio
async def test_sandbox_account_get_products_zeroes_pricing(integration_db):
    with ProductEnv(tenant_id="sandbox-pricing", principal_id="sandbox-principal") as env:
        tenant = TenantFactory(tenant_id="sandbox-pricing", subdomain="sandbox-pricing")
        PrincipalFactory(tenant=tenant, principal_id="sandbox-principal")
        account = AccountFactory(tenant=tenant, account_id="acc_sandbox", sandbox=True)
        _seed_priced_product(tenant)

        response = await env.call_impl(
            buying_mode="brief",
            brief="sandbox product",
            account={"account_id": account.account_id},
            identity=env.identity.model_copy(update={"account_id": account.account_id}),
        )

        assert len(response.products) == 1
        options = [_pricing_inner(option) for option in response.products[0].pricing_options]
        fixed = next(option for option in options if getattr(option, "fixed_price", None) is not None)
        auction = next(option for option in options if getattr(option, "floor_price", None) is not None)
        assert fixed.fixed_price == 0
        assert auction.floor_price == 0
        assert auction.price_guidance.p50 == 0
        assert auction.price_guidance.p75 == 0


@pytest.mark.asyncio
async def test_zero_rate_card_account_get_products_zeroes_pricing(integration_db):
    with ProductEnv(tenant_id="sandbox-rate-card", principal_id="rate-card-principal") as env:
        tenant = TenantFactory(tenant_id="sandbox-rate-card", subdomain="sandbox-rate-card")
        PrincipalFactory(tenant=tenant, principal_id="rate-card-principal")
        account = AccountFactory(
            tenant=tenant,
            account_id="acc_zero_rate_card",
            rate_card=INTERCHANGE_SANDBOX_ZERO_RATE_CARD,
            sandbox=False,
        )
        _seed_priced_product(tenant)

        response = await env.call_impl(
            buying_mode="brief",
            brief="sandbox product",
            account={"account_id": account.account_id},
            identity=env.identity.model_copy(update={"account_id": account.account_id}),
        )

        option = _pricing_inner(response.products[0].pricing_options[0])
        assert option.fixed_price == 0


@pytest.mark.asyncio
async def test_sandbox_account_create_media_buy_traffics_zero_price(monkeypatch, integration_db):
    with ProductEnv(tenant_id="sandbox-create", principal_id="sandbox-create-principal") as env:
        tenant = TenantFactory(tenant_id="sandbox-create", subdomain="sandbox-create", auth_setup_mode=False)
        AdapterConfigFactory(tenant=tenant, adapter_type="mock")
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
        PublisherPartnerFactory(tenant=tenant, is_verified=True)
        PrincipalFactory(tenant=tenant, principal_id="sandbox-create-principal")
        account = AccountFactory(tenant=tenant, account_id="test-acct", sandbox=True)
        product = _seed_priced_product(tenant)

        captured: dict[str, object] = {}

        def capture_adapter_call(request, packages, start_time, end_time, package_pricing_info, *args, **kwargs):
            captured["request"] = request
            captured["packages"] = packages
            captured["package_pricing_info"] = package_pricing_info

            response_package = ResponsePackage(
                package_id=packages[0].package_id,
                product_id=packages[0].product_id,
                budget=packages[0].budget,
            )
            return CreateMediaBuySuccess(media_buy_id="mb_sandbox_traffic", packages=[response_package])

        monkeypatch.setattr(
            "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
            capture_adapter_call,
        )

        from src.core.sandbox import is_sandbox_trafficking_request
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        request_kwargs = {**required_request_kwargs(), "account": {"account_id": account.account_id}}
        request = CreateMediaBuyRequest(
            **request_kwargs,
            brand={"domain": "testbrand.com"},
            start_time=_future(1),
            end_time=_future(3),
            packages=[
                {
                    "product_id": product.product_id,
                    "budget": 250.0,
                    "pricing_option_id": "cpm_usd_auction",
                    "bid_price": 2.23,
                }
            ],
        )

        result = await _create_media_buy_impl(
            req=request,
            identity=env.identity.model_copy(update={"account_id": account.account_id}),
        )

        assert result.response.media_buy_id == "mb_sandbox_traffic"
        assert is_sandbox_trafficking_request(captured["request"])
        captured_packages = captured["packages"]
        assert captured_packages[0].budget == 0.0
        assert captured_packages[0].impressions >= 1

        pricing_info = next(iter(captured["package_pricing_info"].values()))
        assert pricing_info["pricing_model"] == "cpm"
        assert pricing_info["rate"] == 0.0
        assert pricing_info["is_fixed"] is True
        assert pricing_info["bid_price"] is None
        assert pricing_info["sandbox_trafficking"] is True

        from src.core.database.repositories import MediaBuyUoW

        with MediaBuyUoW(tenant.tenant_id) as uow:
            assert uow.media_buys is not None
            persisted = uow.media_buys.get_by_id(result.response.media_buy_id)
            persisted_account_id = persisted.account_id if persisted else None
            persisted_budget = persisted.budget if persisted else None
        assert persisted is not None
        assert persisted_account_id == account.account_id
        assert persisted_budget == Decimal("0.00")
