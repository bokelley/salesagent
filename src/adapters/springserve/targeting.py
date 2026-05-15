"""Translate AdCP targeting into SpringServe demand-tag targeting.

SpringServe demand tags carry targeting on the tag itself (geo, device,
player size, environment, supply-tag inclusion lists, etc.) and inherit
filters from the parent Campaign. The wire shape used here is the
documented JSON contract from the Demand Tag API; Stage 2 refines it
against observed payloads on a real Talpa account.
"""

from __future__ import annotations

from typing import Any


def build_targeting(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the SpringServe demand-tag targeting dict.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model (geo, device, custom).
        product_config: ``SpringServeProductConfig`` as a dict -- supplies
            product-default supply tag inclusion and content filters.
    """
    product_config = product_config or {}
    targeting: dict[str, Any] = {}

    # Product-side inventory inclusion lists translate to demand-tag
    # supply targeting.
    if product_config.get("supply_tag_ids"):
        targeting["allowed_supply_tag_ids"] = list(product_config["supply_tag_ids"])
    if product_config.get("supply_partner_ids"):
        targeting["allowed_supply_partner_ids"] = list(product_config["supply_partner_ids"])
    if product_config.get("player_sizes"):
        targeting["allowed_player_sizes"] = list(product_config["player_sizes"])
    if product_config.get("environments"):
        targeting["allowed_environments"] = list(product_config["environments"])
    if product_config.get("device_types"):
        targeting["allowed_device_types"] = list(product_config["device_types"])

    # AdCP-overlay-driven targeting.
    if targeting_overlay is not None:
        if getattr(targeting_overlay, "geo_countries", None):
            targeting["country_codes"] = [c.root for c in targeting_overlay.geo_countries]
        if getattr(targeting_overlay, "geo_regions", None):
            targeting["region_codes"] = [r.root for r in targeting_overlay.geo_regions]
        if getattr(targeting_overlay, "geo_metros", None):
            metro_values: list[str] = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            if metro_values:
                targeting["dma_codes"] = metro_values
        if getattr(targeting_overlay, "device_type_any_of", None):
            # AdCP device-type overlay wins over product defaults when both
            # are set -- buyer intent is more specific than product defaults.
            targeting["allowed_device_types"] = list(targeting_overlay.device_type_any_of)

    # Raw escape-hatch fields override anything we built up.
    extras = product_config.get("extra_demand_tag_fields") or {}
    if isinstance(extras, dict):
        for key, value in extras.items():
            targeting[key] = value

    return targeting


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for SpringServe.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. The Stage-1 cut rejects
    overlays whose wire format isn't verified against the live API yet --
    Stage 2 narrows this list as fields move from "unverified" to "verified".
    """
    unsupported: list[str] = []
    if targeting_overlay is None:
        return unsupported

    if getattr(targeting_overlay, "geo_postal_areas", None) or getattr(
        targeting_overlay, "geo_postal_areas_exclude", None
    ):
        unsupported.append("Postal-area targeting not supported -- use geo_metros (DMA) or geo_regions instead")

    if getattr(targeting_overlay, "frequency_cap", None):
        unsupported.append(
            "Frequency cap targeting pending SpringServe sandbox validation -- "
            "set frequency caps via SpringServeProductConfig escape hatch for now"
        )

    if getattr(targeting_overlay, "audiences_any_of", None):
        unsupported.append("Audience/segment targeting pending SpringServe sandbox validation")

    if getattr(targeting_overlay, "dayparting", None):
        unsupported.append("Free-form dayparting pending SpringServe sandbox validation")

    return unsupported
