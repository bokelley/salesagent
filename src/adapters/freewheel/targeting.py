"""Translate AdCP targeting into FreeWheel line-item targeting.

FreeWheel models targeting as a structured object on the line item with
``geo``, ``device``, ``customCriteria``, and a reference to a pre-built
``targetingProfileId``. Multiple criteria combine with AND.

The exact wire format is finalised against staging credentials — this
module emits the canonical shape documented in the Publisher API reference
and is exercised by dry-run logging until live calls land.
"""

from __future__ import annotations

from typing import Any


def build_targeting(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the FreeWheel ``targeting`` object for a line item.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model (geo, device, custom).
        product_config: ``FreeWheelProductConfig`` as a dict — supplies
            ``targeting_profile_id`` and product-default ``custom_targeting``.
    """
    product_config = product_config or {}
    targeting: dict[str, Any] = {}

    if product_config.get("targeting_profile_id"):
        targeting["targetingProfileId"] = product_config["targeting_profile_id"]

    if targeting_overlay is not None:
        geo: dict[str, list[str]] = {}
        if getattr(targeting_overlay, "geo_countries", None):
            geo["countries"] = [c.root for c in targeting_overlay.geo_countries]
        if getattr(targeting_overlay, "geo_regions", None):
            geo["regions"] = [r.root for r in targeting_overlay.geo_regions]
        if getattr(targeting_overlay, "geo_metros", None):
            metro_values: list[str] = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            if metro_values:
                geo["metros"] = metro_values
        if geo:
            targeting["geo"] = geo

        if getattr(targeting_overlay, "device_type_any_of", None):
            targeting["deviceTypes"] = list(targeting_overlay.device_type_any_of)

    # Custom key-value targeting: package overrides product defaults
    custom: dict[str, list[str]] = dict(product_config.get("custom_targeting", {}) or {})
    if targeting_overlay is not None and getattr(targeting_overlay, "custom", None):
        package_custom = targeting_overlay.custom.get("freewheel", {}) or {}
        for key, values in package_custom.items():
            custom[key] = list(values)
    if custom:
        targeting["customCriteria"] = [{"key": k, "values": v} for k, v in custom.items()]

    return targeting


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for FreeWheel.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. Frequency cap, audience,
    and dayparting overlays are rejected pending sandbox-validated translation
    to FreeWheel's native shapes — until the Publisher API JSON contract is
    locked in (see docs/adapters/freewheel/README.md), passing them through
    would risk shipping the wrong wire format.
    """
    unsupported: list[str] = []
    if targeting_overlay is None:
        return unsupported

    if getattr(targeting_overlay, "geo_postal_areas", None) or getattr(
        targeting_overlay, "geo_postal_areas_exclude", None
    ):
        unsupported.append("Postal-area targeting not supported — use geo_metros (DMA) or geo_regions instead")

    if getattr(targeting_overlay, "frequency_cap", None):
        unsupported.append(
            "Frequency cap targeting pending FreeWheel sandbox validation — "
            "set frequency caps directly via FreeWheelProductConfig for now"
        )

    if getattr(targeting_overlay, "audiences_any_of", None):
        unsupported.append("Audience/segment targeting pending FreeWheel sandbox validation")

    if getattr(targeting_overlay, "dayparting", None):
        unsupported.append(
            "Free-form dayparting pending FreeWheel sandbox validation — "
            "use a pre-built FreeWheel targeting profile via FreeWheelProductConfig.targeting_profile_id"
        )

    return unsupported
