"""Mock seller platform for storyboard validation and dev.

Equivalent to ``src/adapters/mock_ad_server.py`` but expressed as a
``DecisioningPlatform`` subclass. The framework already ships
``adcp.decisioning.mock_ad_server`` — we use that for the bits we don't need
to customize, and override ``get_products`` to read real product rows from
the existing salesagent DB.

First milestone target: pass the ``media_buy_seller`` storyboard's
``get_products`` step.

Skeleton.
"""

from __future__ import annotations


# from adcp.decisioning import DecisioningCapabilities, DecisioningPlatform
# from adcp.decisioning.capabilities import (
#     Account,
#     Adcp,
#     IdempotencySupported,
#     MediaBuy,
#     Specialism,
# )
# from adcp.types import GetProductsRequest, GetProductsResponse, Product

# from core.stores.accounts import SalesagentAccountStore


# class MockSellerPlatform(DecisioningPlatform):
#     """Reads products from the salesagent ``products`` table; everything
#     else is mocked via the framework's mock ad server reference impl."""
#
#     accounts = SalesagentAccountStore()
#     capabilities = DecisioningCapabilities(
#         specialisms=[Specialism.sales_non_guaranteed.value],
#         adcp=Adcp(
#             major_versions=[3],
#             idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=86400),
#         ),
#         account=Account(supported_billing=["operator"]),
#         media_buy=MediaBuy(supported_pricing_models=["cpm"]),
#     )
#
#     async def get_products(self, ctx, request: GetProductsRequest) -> GetProductsResponse:
#         """Read products for the resolved tenant from the existing schema.
#
#         ``ctx.account.metadata['tenant_id']`` is set by SubdomainTenantMiddleware
#         + the AccountStore. Filter ``Product`` rows on it.
#         """
#         ...
