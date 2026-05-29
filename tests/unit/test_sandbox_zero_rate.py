from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.resolved_product import ResolvedProduct
from src.core.sandbox_zero_rate import INTERCHANGE_SANDBOX_ZERO_RATE_CARD
from src.core.schemas import GetProductsRequest
from src.core.testing_hooks import AdCPTestContext
from tests.helpers.adcp_factories import create_test_product
from tests.unit.test_create_media_buy_behavioral import _make_request, _PatchContext


class _FakeAccountUoW:
    def __init__(self, tenant_id: str, account: object):
        self.tenant_id = tenant_id
        self.account = account
        self.accounts = MagicMock()
        self.accounts.get_by_id.return_value = account

    def __enter__(self):
        return self

    def __exit__(self, *args):
        detach = getattr(self.account, "detach", None)
        if detach is not None:
            detach()


class _DetachingAccount:
    def __init__(self, account_id: str, sandbox: bool, rate_card: str | None):
        self._account_id = account_id
        self._sandbox = sandbox
        self._rate_card = rate_card
        self._detached = False

    def detach(self) -> None:
        self._detached = True

    def _get(self, field_name: str):
        if self._detached:
            raise RuntimeError(f"{field_name} read after account detached")
        return getattr(self, f"_{field_name}")

    @property
    def account_id(self) -> str:
        return self._get("account_id")

    @property
    def sandbox(self) -> bool:
        return self._get("sandbox")

    @property
    def rate_card(self) -> str | None:
        return self._get("rate_card")


class _FakeProductUoW:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.products = MagicMock()
        self.products.list_all.return_value = [object()]
        self.session = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _sandbox_account() -> _DetachingAccount:
    return _DetachingAccount(
        account_id="acct_sandbox",
        sandbox=True,
        rate_card=INTERCHANGE_SANDBOX_ZERO_RATE_CARD,
    )


@pytest.mark.asyncio
async def test_get_products_zeroes_pricing_for_sandbox_rate_card_account():
    from src.core.tools.products import _get_products_impl

    product = create_test_product(
        product_id="prod_sandbox",
        pricing_options=[
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "pricing_option_id": "cpm_usd_fixed",
                "fixed_price": 12.50,
                "min_spend_per_package": 1000.0,
            },
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "pricing_option_id": "cpm_usd_auction",
                "floor_price": 8.0,
                "price_guidance": {"p25": 10.0, "p50": 12.0, "p75": 15.0},
            },
        ],
    )
    resolved = ResolvedProduct(wire=product)
    request = GetProductsRequest(buying_mode="brief", brief="display ads", brand={"domain": "buyer.example"})
    identity = ResolvedIdentity(
        principal_id="principal_1",
        tenant_id="tenant_1",
        tenant={"tenant_id": "tenant_1", "ad_server": "mock"},
        protocol="mcp",
        account_id="acct_sandbox",
    )

    adapter = MagicMock()
    adapter.get_supported_pricing_models.return_value = ["cpm"]

    with (
        patch("src.core.database.repositories.uow.ProductUoW", _FakeProductUoW),
        patch(
            "src.core.sandbox_zero_rate.AccountUoW", lambda tenant_id: _FakeAccountUoW(tenant_id, _sandbox_account())
        ),
        patch("src.core.tools.products.convert_product_models_to_resolved", return_value=[resolved]),
        patch("src.core.tools.products.get_principal_object", return_value=MagicMock()),
        patch("src.core.helpers.adapter_helpers.get_adapter", return_value=adapter),
        patch("src.core.tools.products.get_audit_logger") as audit_logger,
        patch("src.services.dynamic_products.generate_variants_for_brief", new=AsyncMock(return_value=[])),
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as pricing_service,
    ):
        pricing_service.return_value.enrich_products_with_pricing.side_effect = lambda products, **_: products
        audit_logger.return_value.log_operation.return_value = None
        response = await _get_products_impl(request, identity)

    assert response.sandbox is True
    assert response.ext.model_dump(mode="json") == {
        "salesagent_sandbox": {
            "account_id": "acct_sandbox",
            "sandbox": True,
            "rate_card": INTERCHANGE_SANDBOX_ZERO_RATE_CARD,
            "reason": "sandbox_account+zero_rate_card",
            "pricing": "zero",
            "spend": "dry_run",
        }
    }
    fixed = response.products[0].pricing_options[0].root
    auction = response.products[0].pricing_options[1].root
    assert fixed.fixed_price == 0.0
    assert fixed.min_spend_per_package == 0.0
    assert auction.floor_price == 0.0
    assert auction.price_guidance.p25 == 0.0
    assert auction.price_guidance.p50 == 0.0
    assert auction.price_guidance.p75 == 0.0
    assert response.products[0].ext.model_dump(mode="json")["salesagent_sandbox"]["spend"] == "dry_run"


@pytest.mark.asyncio
async def test_create_media_buy_for_sandbox_rate_card_forces_dry_run_without_adapter_creation():
    from src.core.tools.media_buy_create import _create_media_buy_impl

    req = _make_request()
    catalog_product = ResolvedProduct(wire=create_test_product(product_id="prod_1"))
    adapter = MagicMock()
    adapter.manual_approval_required = False
    adapter.manual_approval_operations = []
    adapter.validate_media_buy_request.return_value = []

    with (
        _PatchContext() as pc,
        patch(
            "src.core.sandbox_zero_rate.AccountUoW", lambda tenant_id: _FakeAccountUoW(tenant_id, _sandbox_account())
        ),
        patch("src.core.helpers.account_provisioning.resolve_account_advertiser") as resolve_advertiser,
        patch("src.core.tools.media_buy_create.get_adapter", return_value=adapter),
        patch("src.core.tools.products.get_product_catalog", return_value=[catalog_product]),
    ):
        identity = pc.identity.model_copy(
            update={
                "account_id": "acct_sandbox",
                "testing_context": AdCPTestContext(dry_run=False),
            }
        )
        result = await _create_media_buy_impl(req=req, identity=identity)

    resolve_advertiser.assert_not_called()
    adapter.create_media_buy.assert_not_called()
    assert result.status == "completed"
    assert result.response.media_buy_id.startswith("dry_run_")
