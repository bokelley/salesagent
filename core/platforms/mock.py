"""Mock seller platform — first milestone target.

Subclasses ``DecisioningPlatform`` and implements the five required
``sales-non-guaranteed`` methods. ``get_products`` reads real product
rows from the existing salesagent ``products`` table, scoped to the
resolved tenant. The other four methods stub fast-path responses
sufficient for the ``media_buy_seller`` storyboard.
"""

from __future__ import annotations

from typing import Any

from adcp.decisioning import (
    AdcpError,
    DecisioningCapabilities,
    DecisioningPlatform,
    RequestContext,
)
from adcp.decisioning.capabilities import (
    Account as CapabilitiesAccount,
)
from adcp.decisioning.capabilities import (
    Adcp,
    IdempotencySupported,
    MediaBuy,
    SupportedProtocol,
)
from sqlalchemy import select

from core.stores.accounts import SalesagentAccountStore
from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ProductRow


class MockSellerPlatform(DecisioningPlatform):
    """Reads products from the salesagent ``products`` table; everything
    else is a stub fast-path sufficient for storyboard validation."""

    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(
                supported=True, replay_ttl_seconds=86400
            ),
        ),
        account=CapabilitiesAccount(supported_billing=["operator"]),
        media_buy=MediaBuy(supported_pricing_models=["cpm"]),
        supported_protocols=[SupportedProtocol.media_buy],
    )
    accounts = SalesagentAccountStore()

    def get_products(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        tenant_id = ctx.account.metadata.get("tenant_id")
        if not tenant_id:
            raise AdcpError(
                "ACCOUNT_NOT_FOUND",
                message="Resolved account is missing tenant_id metadata",
                recovery="terminal",
                field="account",
            )

        with get_db_session() as session:
            rows = session.scalars(
                select(ProductRow).filter_by(tenant_id=tenant_id)
            ).all()

        return {
            "products": [_product_to_wire(row) for row in rows],
        }

    def create_media_buy(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        packages = self._get_packages(req)
        if not packages:
            raise AdcpError(
                "INVALID_REQUEST",
                message="At least one package is required",
                field="packages",
                recovery="correctable",
            )
        tenant_id = ctx.account.metadata.get("tenant_id", "unknown")
        return {
            "media_buy_id": f"mb_{tenant_id}_{len(packages)}",
            "status": "active",
            "packages": [
                {
                    "package_id": f"pkg_{i}",
                    "product_id": pkg.get("products", ["unknown"])[0]
                    if isinstance(pkg.get("products"), list)
                    else pkg.get("product_id", "unknown"),
                    "pricing_option_id": pkg.get(
                        "pricing_option_id", "po-cpm-default"
                    ),
                }
                for i, pkg in enumerate(packages)
            ],
        }

    def update_media_buy(
        self,
        media_buy_id: str,
        patch: Any,  # noqa: ARG002 — patch echoed as no-op
        ctx: RequestContext[Any],  # noqa: ARG002 — ctx unused in stub
    ) -> dict[str, Any]:
        return {
            "media_buy_id": media_buy_id,
            "status": "active",
            "packages": [],
        }

    def sync_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],  # noqa: ARG002 — ctx unused in stub
    ) -> dict[str, Any]:
        creatives = getattr(req, "creatives", None) or []
        return {
            "creatives": [
                {
                    "creative_id": (
                        c.creative_id
                        if hasattr(c, "creative_id")
                        else c.get("creative_id")
                    ),
                    "approval_status": "approved",
                }
                for c in creatives
            ],
        }

    def get_media_buy_delivery(
        self,
        req: Any,
        ctx: RequestContext[Any],  # noqa: ARG002 — ctx unused in stub
    ) -> dict[str, Any]:
        media_buy_id = getattr(req, "media_buy_id", None) or "mb_unknown"
        return {
            "media_buy_deliveries": [
                {
                    "media_buy_id": media_buy_id,
                    "totals": {"impressions": 0, "spend": 0.0},
                },
            ],
        }

    @staticmethod
    def _get_packages(req: Any) -> list[dict[str, Any]]:
        if hasattr(req, "packages"):
            packages = req.packages or []
            return [
                p.model_dump() if hasattr(p, "model_dump") else dict(p)
                for p in packages
            ]
        if isinstance(req, dict):
            return list(req.get("packages") or [])
        return []


def _product_to_wire(row: ProductRow) -> dict[str, Any]:
    """Project a salesagent Product ORM row to the AdCP wire shape.

    Maps only the fields the spec requires for ``get_products``; richer
    fields (price_guidance, measurement, creative_policy, etc.) follow
    in M2 once the storyboard exercises them.
    """
    publisher_properties = _publisher_properties_from_row(row)
    return {
        "product_id": row.product_id,
        "name": row.name,
        "description": row.description or "",
        "delivery_type": row.delivery_type,
        "publisher_properties": publisher_properties,
        "format_ids": row.format_ids or [],
        "pricing_options": [
            {
                "pricing_option_id": "po-cpm-default",
                "pricing_model": "cpm",
                "floor_price": 1.0,
                "currency": "USD",
            }
        ],
        "reporting_capabilities": {
            "available_metrics": ["impressions", "spend"],
            "available_reporting_frequencies": ["daily"],
            "date_range_support": "date_range",
            "supports_webhooks": False,
            "expected_delay_minutes": 60,
            "timezone": "UTC",
        },
        "delivery_measurement": row.delivery_measurement
        or {"provider": "publisher"},
    }


_DEFAULT_PUBLISHER_DOMAIN = "example.com"


def _publisher_properties_from_row(row: ProductRow) -> list[dict[str, Any]]:
    """Convert salesagent's properties/property_ids/property_tags
    XOR-constrained columns into the AdCP wire ``publisher_properties``
    list per spec (PublisherPropertiesAll/ById/ByTag selectors).

    Each selector requires ``publisher_domain`` (the host of the
    publisher's ``adagents.json``); salesagent doesn't track a
    publisher_domain per product/tag/id today, so we default to
    ``example.com`` as a placeholder. M2 should source this from the
    tenant's ``virtual_host``/``setup_domain`` config.
    """
    if row.property_tags:
        return [
            {
                "publisher_domain": _DEFAULT_PUBLISHER_DOMAIN,
                "selection_type": "by_tag",
                "property_tags": list(row.property_tags),
            }
        ]
    if row.property_ids:
        return [
            {
                "publisher_domain": _DEFAULT_PUBLISHER_DOMAIN,
                "selection_type": "by_id",
                "property_ids": list(row.property_ids),
            }
        ]
    if row.properties:
        return [
            {
                "publisher_domain": prop.get("publisher_domain", _DEFAULT_PUBLISHER_DOMAIN),
                "selection_type": "all",
            }
            for prop in row.properties
        ]
    return [
        {"publisher_domain": _DEFAULT_PUBLISHER_DOMAIN, "selection_type": "all"}
    ]
