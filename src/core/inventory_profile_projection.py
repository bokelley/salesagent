"""Project inventory profiles into buyer-facing wholesale products."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from adcp.types import DeliveryForecast
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm.attributes import set_committed_value

from src.core.database.models import (
    PRODUCT_REPORTING_CAPABILITIES_DEFAULT,
    CurrencyLimit,
    InventoryProfile,
    PricingOption,
    Product,
)
from src.core.product_conversion import convert_product_model_to_resolved
from src.core.resolved_product import ResolvedProduct

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_WHOLESALE_PRICE_GUIDANCE: dict[str, float] = {"floor": 0.0}
WHOLESALE_PRICE_GUIDANCE_PERCENTILES = frozenset({"p25", "p50", "p75", "p90"})
WHOLESALE_PROFILE_MANAGED_BY = "wholesale_products_api"


def is_complete_inventory_profile(profile: InventoryProfile) -> bool:
    """Return whether a bundle has the minimum Product wire shape."""
    return bool(profile.format_ids) and bool(profile.publisher_properties)


def inventory_profile_status(profile: InventoryProfile) -> str:
    """Return the storefront-managed lifecycle status for a bundle."""
    constraints = profile.constraints if isinstance(profile.constraints, dict) else {}
    return str(constraints.get("status") or "active")


def is_buyer_visible_inventory_profile(profile: InventoryProfile) -> bool:
    """Return whether an inventory bundle should be exposed to buyers."""
    return is_complete_inventory_profile(profile) and inventory_profile_status(profile) not in {"draft", "archived"}


def is_wholesale_owned_inventory_profile(profile: InventoryProfile, product_id: str | None = None) -> bool:
    """Return whether a bundle is owned by the storefront wholesale-products API."""
    constraints = profile.constraints if isinstance(profile.constraints, dict) else {}
    if constraints.get("managed_by") != WHOLESALE_PROFILE_MANAGED_BY:
        return False
    return product_id is None or constraints.get("owner_product_id") == product_id


def is_materialized_wholesale_product(product: Product) -> bool:
    """Return whether a legacy Product row is only a wholesale-bundle materialization."""
    profile = getattr(product, "inventory_profile", None)
    constraints = getattr(profile, "constraints", None)
    return isinstance(constraints, dict) and constraints.get("managed_by") == WHOLESALE_PROFILE_MANAGED_BY


def default_wholesale_currency(
    currency_limits: list[CurrencyLimit],
    *,
    fallback: str = "USD",
    preferred: str | None = None,
) -> str:
    """Pick a stable default currency for bundle-backed auction CPM pricing."""
    currency_codes = sorted(
        {
            str(limit.currency_code).upper()
            for limit in currency_limits
            if getattr(limit, "currency_code", None) and str(limit.currency_code).strip()
        }
    )
    preferred_code = preferred.upper() if preferred else None
    if preferred_code and (not currency_codes or preferred_code in currency_codes):
        return preferred_code
    fallback_code = fallback.upper()
    if fallback_code in currency_codes:
        return fallback_code
    return currency_codes[0] if currency_codes else fallback_code


def inventory_profile_to_product_model(profile: InventoryProfile, *, default_currency: str) -> Product:
    """Build a transient Product model for a wholesale inventory bundle.

    The returned Product is deliberately not added to a session. It gives shared
    Product conversion, filtering, and forecasting code a product-shaped target
    while preserving InventoryProfile as the durable wholesale primitive.
    """
    constraints = profile.constraints if isinstance(profile.constraints, dict) else {}
    status = inventory_profile_status(profile)
    product = Product(
        tenant_id=profile.tenant_id,
        product_id=profile.profile_id,
        name=profile.name,
        description=profile.description,
        format_ids=profile.format_ids,
        targeting_template=profile.targeting_template or {},
        delivery_type="non_guaranteed",
        channels=constraints.get("channels") or None,
        implementation_config={
            "source": "inventory_profile",
            "status": status,
        },
        properties=None,
        property_tags=None,
        inventory_profile_id=profile.id,
        delivery_measurement={"provider": "publisher"},
        reporting_capabilities=dict(PRODUCT_REPORTING_CAPABILITIES_DEFAULT),
        property_targeting_allowed=bool(constraints.get("targeting_dimensions")),
        signal_targeting_allowed=True,
        forecast=system_owned_profile_forecast(profile),
        allowed_principal_ids=constraints.get("allowed_principal_ids") or None,
        allowed_actions=constraints.get("allowed_actions") or None,
        format_options=constraints.get("format_options") or None,
        vendor_metric_optimization=constraints.get("vendor_metric_optimization") or None,
    )
    product.pricing_options = [
        PricingOption(
            tenant_id=profile.tenant_id,
            product_id=profile.profile_id,
            pricing_model="cpm",
            rate=None,
            currency=default_currency.upper(),
            is_fixed=False,
            price_guidance=_wholesale_price_guidance(profile),
            min_spend_per_package=None,
        )
    ]
    set_committed_value(product, "inventory_profile", profile)
    return product


def project_visible_inventory_profile_product(
    session: Session,
    tenant_id: str,
    product_id: str,
    *,
    default_currency: str | None = None,
) -> Product | None:
    """Resolve a buyer-visible inventory bundle as a transient Product."""
    from src.core.database.repositories import (
        AdapterConfigRepository,
        CurrencyLimitRepository,
        InventoryProfileRepository,
    )

    profile = InventoryProfileRepository(session, tenant_id).get_by_id(product_id)
    if not isinstance(profile, InventoryProfile) or not is_buyer_visible_inventory_profile(profile):
        return None
    adapter_config = AdapterConfigRepository(session, tenant_id).find_by_tenant()
    preferred_currency = (
        adapter_config.gam_network_currency
        if adapter_config is not None
        and adapter_config.adapter_type == "google_ad_manager"
        and adapter_config.gam_network_currency
        else None
    )
    currency = default_currency or default_wholesale_currency(
        CurrencyLimitRepository(session, tenant_id).list_all(),
        preferred=preferred_currency,
    )
    return inventory_profile_to_product_model(profile, default_currency=currency)


def inventory_profiles_to_resolved_products(
    profiles: list[InventoryProfile],
    *,
    adapter_type: str | None,
    default_currency: str,
) -> list[ResolvedProduct]:
    """Project complete inventory profiles into spec-clean wholesale products."""
    return [
        convert_product_model_to_resolved(
            inventory_profile_to_product_model(profile, default_currency=default_currency),
            adapter_type=adapter_type,
        )
        for profile in profiles
        if is_buyer_visible_inventory_profile(profile)
    ]


def _wholesale_price_guidance(profile: InventoryProfile) -> dict[str, float]:
    guidance: dict[str, float] = {}
    for key, value in _profile_cpm_analytics(profile).items():
        if key not in WHOLESALE_PRICE_GUIDANCE_PERCENTILES:
            continue
        numeric_value = _optional_float(value)
        if numeric_value is not None:
            guidance[key] = numeric_value
    guidance["floor"] = DEFAULT_WHOLESALE_PRICE_GUIDANCE["floor"]
    return guidance


def _profile_cpm_analytics(profile: InventoryProfile) -> dict[str, Any]:
    analytics = profile.pricing_availability if isinstance(profile.pricing_availability, dict) else {}
    by_model = analytics.get("pricing_guidance_by_model")
    if not isinstance(by_model, dict):
        return {}
    cpm = by_model.get("cpm")
    return cpm if isinstance(cpm, dict) else {}


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def system_owned_profile_forecast(profile: InventoryProfile) -> dict[str, Any] | None:
    """Return valid system-owned forecast metadata, omitting stale legacy shapes."""
    if profile.forecast is None:
        return None
    try:
        return DeliveryForecast.model_validate(profile.forecast).model_dump(mode="json", exclude_none=True)
    except PydanticValidationError:
        logger.warning(
            "Ignoring invalid system-owned forecast metadata for inventory profile %s/%s",
            profile.tenant_id,
            profile.profile_id,
            exc_info=True,
        )
        return None
