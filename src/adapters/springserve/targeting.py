"""Translate AdCP targeting into SpringServe demand-tag fields.

SpringServe demand tags don't wrap targeting in a nested ``targeting``
object -- the targeting fields (``country_codes``, ``state_codes``,
``metro_area_codes``, ``player_sizes``, ``user_agent_devices``, and the
supply-side ``demand_tag_priorities``) live directly on the tag, each
paired with a ``<dimension>_targeting`` discriminator (``"All"`` vs
``"White List"``).

This module produces the kwarg dict consumed by
:class:`SpringServeDemandTagsClient.create` so adapter code doesn't have
to know the wire-format conventions.
"""

from __future__ import annotations

from typing import Any


def build_demand_tag_targeting(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the targeting kwargs for ``SpringServeDemandTagsClient.create``.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model (geo, device, custom).
        product_config: ``SpringServeProductConfig`` as a dict -- supplies
            product-default supply tag inclusion, player sizes, and
            environments.

    Output keys correspond 1:1 to Demand Tag fields. Empty values are
    omitted so the client builder doesn't override SpringServe defaults.
    """
    product_config = product_config or {}
    kwargs: dict[str, Any] = {}

    # Supply targeting -- product config carries supply_tag_ids, we turn them
    # into demand_tag_priorities entries (priority + tier default to 1).
    supply_tag_ids = product_config.get("supply_tag_ids") or []
    if supply_tag_ids:
        kwargs["demand_tag_priorities"] = [
            {"supply_tag_id": int(stid), "priority": 1, "tier": 1} for stid in supply_tag_ids
        ]

    # Player + device defaults from product config.
    if product_config.get("player_sizes"):
        kwargs["player_sizes"] = list(product_config["player_sizes"])
    if product_config.get("device_types"):
        kwargs["user_agent_devices"] = list(product_config["device_types"])

    # AdCP-overlay-driven geo. Empty lists in the overlay are no-ops; we
    # only set targeting when there's actually a list.
    if targeting_overlay is not None:
        if getattr(targeting_overlay, "geo_countries", None):
            kwargs["country_codes"] = [c.root for c in targeting_overlay.geo_countries]
        if getattr(targeting_overlay, "geo_regions", None):
            kwargs["state_codes"] = [r.root for r in targeting_overlay.geo_regions]
        if getattr(targeting_overlay, "geo_metros", None):
            metro_values: list[str] = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            if metro_values:
                kwargs["metro_area_codes"] = metro_values
        if getattr(targeting_overlay, "device_type_any_of", None):
            # AdCP device-type overlay wins over product defaults when both
            # are set -- buyer intent is more specific than product defaults.
            kwargs["user_agent_devices"] = list(targeting_overlay.device_type_any_of)

    # Escape hatch -- raw demand-tag field overrides (extras win).
    extras = product_config.get("extra_demand_tag_fields") or {}
    if isinstance(extras, dict):
        for key, value in extras.items():
            kwargs[key] = value

    return kwargs


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for SpringServe.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. The Stage-2 cut rejects
    overlays whose wire format isn't verified against the live API yet --
    subsequent stages narrow this list as fields move from "unverified" to
    "verified" against the live account.
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


# Backwards-compatible alias for callers still importing the old name.
build_targeting = build_demand_tag_targeting
