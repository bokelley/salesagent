"""GAM-backed seller platform — M3 first stake.

Subclasses ``DecisioningPlatform`` and implements ``get_products`` by
reading ``Placement`` rows from Google Ad Manager and projecting each
to an AdCP ``Product`` wire shape. The other four required methods
remain stubs sufficient for storyboard validation; M3 wave 2 wires
``create_media_buy`` → real GAM ``Order`` + ``LineItem`` creation.

Tenant binding: each ``DecisioningPlatform`` instance is per-tenant,
keyed in ``PlatformRouter.platforms`` by ``tenant_id``. ``ctx.account.metadata['tenant_id']``
is used to scope GAM client construction (caching one client per tenant
avoids re-fetching credentials on every request).
"""

from __future__ import annotations

import logging
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
from googleads import ad_manager

from core.platforms._gam_client import get_gam_client
from core.stores.accounts import SalesagentAccountStore

logger = logging.getLogger(__name__)


class WonderstruckGamPlatform(DecisioningPlatform):
    """Reads real Placements from a tenant's GAM network and projects
    each to an AdCP Product. Stubs the other four required methods."""

    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=86400),
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

        client = get_gam_client(tenant_id)
        placements = _list_active_placements(client)
        ad_unit_index = _index_ad_units(client)

        products = [
            _placement_to_product(p, ad_unit_index, tenant_id)
            for p in placements
        ]
        logger.info(
            f"GAM tenant={tenant_id} returning {len(products)} products "
            f"({len(placements)} placements, {len(ad_unit_index)} ad units)"
        )
        return {"products": products}

    def create_media_buy(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        # M3 wave 2 — create real GAM Order + LineItems here. For the
        # first stake we accept and echo, same shape as MockSellerPlatform.
        packages = _extract_packages(req)
        if not packages:
            raise AdcpError(
                "INVALID_REQUEST",
                message="At least one package is required",
                field="packages",
                recovery="correctable",
            )
        tenant_id = ctx.account.metadata.get("tenant_id", "unknown")
        return {
            "media_buy_id": f"gam_{tenant_id}_{len(packages)}",
            "status": "pending_start",
            "packages": [
                {
                    "package_id": f"pkg_{i}",
                    "product_id": (pkg.get("products") or ["unknown"])[0]
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
        patch: Any,  # noqa: ARG002
        ctx: RequestContext[Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        return {"media_buy_id": media_buy_id, "status": "active", "packages": []}

    def sync_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],  # noqa: ARG002
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
        ctx: RequestContext[Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        media_buy_id = getattr(req, "media_buy_id", None) or "mb_unknown"
        return {
            "media_buy_deliveries": [
                {
                    "media_buy_id": media_buy_id,
                    "totals": {"impressions": 0, "spend": 0.0},
                }
            ],
        }


# ──────────────────────────── helpers ─────────────────────────────


def _list_active_placements(client: ad_manager.AdManagerClient) -> list[Any]:
    svc = client.GetService("PlacementService")
    stmt = (
        ad_manager.StatementBuilder()
        .Where("status = 'ACTIVE'")
        .OrderBy("id", ascending=False)
        .Limit(50)
        .ToStatement()
    )
    result = svc.getPlacementsByStatement(stmt)
    return list(result.results or [])


def _index_ad_units(client: ad_manager.AdManagerClient) -> dict[int, Any]:
    """Map id → ad-unit so a Placement's targetedAdUnitIds can be resolved."""
    svc = client.GetService("InventoryService")
    out: dict[int, Any] = {}
    offset = 0
    page_size = 200
    while True:
        stmt = (
            ad_manager.StatementBuilder()
            .Where("status = 'ACTIVE'")
            .Limit(page_size)
            .Offset(offset)
            .ToStatement()
        )
        page = svc.getAdUnitsByStatement(stmt)
        results = list(page.results or [])
        for au in results:
            out[au.id] = au
        if len(results) < page_size:
            break
        offset += page_size
    return out


def _placement_to_product(
    placement: Any, ad_unit_index: dict[int, Any], tenant_id: str
) -> dict[str, Any]:
    """Project a GAM Placement to AdCP Product wire shape.

    Each placement aggregates ad units; we surface the union of ad-unit
    sizes as the format set, and synthesize a single CPM pricing option.
    """
    targeted_ad_unit_ids = list(getattr(placement, "targetedAdUnitIds", None) or [])
    sizes = _collect_sizes(targeted_ad_unit_ids, ad_unit_index)
    format_ids = [
        {
            "agent_url": "https://creative.adcontextprotocol.org/",
            "id": _size_to_format_id(size),
        }
        for size in sizes
    ] or [
        {
            "agent_url": "https://creative.adcontextprotocol.org/",
            "id": "display_300x250",
        }
    ]

    return {
        "product_id": f"gam_placement_{placement.id}",
        "name": placement.name,
        "description": getattr(placement, "description", None)
        or f"GAM placement {placement.id}",
        "delivery_type": "non_guaranteed",
        "publisher_properties": [
            {
                "publisher_domain": _publisher_domain(tenant_id),
                "selection_type": "all",
            }
        ],
        "format_ids": format_ids,
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
        "delivery_measurement": {"provider": "publisher"},
    }


def _collect_sizes(
    ad_unit_ids: list[int], index: dict[int, Any]
) -> list[tuple[int, int]]:
    """Union of ``adUnitSizes[].size.{width,height}`` across the ad units a placement targets."""
    out: set[tuple[int, int]] = set()
    for au_id in ad_unit_ids:
        au = index.get(au_id)
        if au is None:
            continue
        for s in getattr(au, "adUnitSizes", None) or []:
            size = getattr(s, "size", None)
            if size is None:
                continue
            try:
                out.add((int(size.width), int(size.height)))
            except (AttributeError, TypeError, ValueError):
                continue
    # Sort largest-area-first so the most-prominent slot leads.
    return sorted(out, key=lambda wh: -(wh[0] * wh[1]))


def _size_to_format_id(size: tuple[int, int]) -> str:
    w, h = size
    return f"display_{w}x{h}"


def _publisher_domain(tenant_id: str) -> str:
    """Stub publisher_domain mapping. M3 wave 2 sources this from the
    tenant's virtual_host or a per-tenant config column."""
    return f"{tenant_id}.example.com"


def _extract_packages(req: Any) -> list[dict[str, Any]]:
    if hasattr(req, "packages"):
        packages = req.packages or []
        return [
            p.model_dump() if hasattr(p, "model_dump") else dict(p)
            for p in packages
        ]
    if isinstance(req, dict):
        return list(req.get("packages") or [])
    return []
