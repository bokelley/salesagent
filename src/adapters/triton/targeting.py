"""Translate AdCP targeting into TAP flight ``targetingRules``.

TAP expresses targeting as an array of rules of the form::

    {"type": "in" | "not in", "dimension": "<dim>", "values": [...]}

where ``dimension`` is one of ``station``, ``station-group``,
``station-genre-shoutcast``, ``country``, ``state``, ``market``, etc. Multiple
rules combine with AND. Station selection is the primary inventory dimension;
country/state/market are the geo overlay.
"""

from __future__ import annotations

from typing import Any


def _rule(dimension: str, values: list[str], *, exclude: bool = False) -> dict[str, Any]:
    return {
        "type": "not in" if exclude else "in",
        "dimension": dimension,
        "values": values,
    }


def build_targeting_rules(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the TAP ``targetingRules`` array for a flight.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model with geo and custom fields.
            Per-package custom override at ``custom["triton"]["station_ids"]``
            wins over the product-level station list.
        product_config: Per-product ``TritonProductConfig`` as a dict. Provides
            the default station/genre/daypart selection when the request
            doesn't override.
    """
    rules: list[dict[str, Any]] = []
    product_config = product_config or {}
    custom: dict[str, Any] = {}
    if targeting_overlay is not None and getattr(targeting_overlay, "custom", None):
        custom = targeting_overlay.custom.get("triton", {}) or {}

    # Station selection — package custom override beats product default
    station_ids = custom.get("station_ids") or product_config.get("station_ids") or []
    if station_ids:
        rules.append(_rule("station", [str(s) for s in station_ids]))

    station_group_ids = custom.get("station_group_ids") or product_config.get("station_group_ids") or []
    if station_group_ids:
        rules.append(_rule("station-group", [str(s) for s in station_group_ids]))

    genres = custom.get("genres") or product_config.get("genres") or []
    if genres:
        rules.append(_rule("station-genre-shoutcast", list(genres)))

    stream_types = custom.get("stream_types") or product_config.get("stream_types") or []
    if stream_types:
        rules.append(_rule("stream-type", list(stream_types)))

    daypart_ids = custom.get("daypart_ids") or product_config.get("daypart_ids") or []
    if daypart_ids:
        rules.append(_rule("daypart", [str(d) for d in daypart_ids]))

    # Geo overlay
    if targeting_overlay is not None:
        if getattr(targeting_overlay, "geo_countries", None):
            rules.append(_rule("country", [c.root for c in targeting_overlay.geo_countries]))
        if getattr(targeting_overlay, "geo_regions", None):
            rules.append(_rule("state", [r.root for r in targeting_overlay.geo_regions]))
        if getattr(targeting_overlay, "geo_metros", None):
            metro_values: list[str] = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            if metro_values:
                rules.append(_rule("market", metro_values))

    return rules


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for TAP.

    TAP is audio-only: video/display/CTV-specific targeting dimensions don't
    apply. IAB content categories don't map onto TAP's content model — use
    genre targeting via product config instead.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. The ``daypart`` overlay
    is rejected because TAP dayparts are pre-built entities referenced by ID
    via product config — there's no way to translate a free-form daypart spec
    here.
    """
    unsupported: list[str] = []
    if targeting_overlay is None:
        return unsupported

    if getattr(targeting_overlay, "device_type_any_of", None):
        for device in targeting_overlay.device_type_any_of:
            if device not in {"mobile", "desktop", "audio"}:
                unsupported.append(f"Device type '{device}' not supported (Triton serves audio-capable devices only)")

    if getattr(targeting_overlay, "media_type_any_of", None):
        non_audio = [m for m in targeting_overlay.media_type_any_of if m != "audio"]
        if non_audio:
            unsupported.append(f"Media types {non_audio} not supported (Triton is audio-only)")

    if getattr(targeting_overlay, "content_cat_any_of", None):
        unsupported.append(
            "IAB content categories not supported — use product-level genre targeting (TritonProductConfig.genres)"
        )

    if getattr(targeting_overlay, "browser_any_of", None):
        unsupported.append("Browser targeting not supported (audio platform)")

    if getattr(targeting_overlay, "frequency_cap", None):
        unsupported.append(
            "Frequency cap targeting not yet supported — file a follow-up if your TAP account uses flight-level caps"
        )

    if getattr(targeting_overlay, "audiences_any_of", None):
        unsupported.append("Audience/segment targeting not supported by the Triton adapter")

    if getattr(targeting_overlay, "dayparting", None):
        unsupported.append(
            "Free-form dayparting not supported — reference pre-built TAP daypart entities via "
            "TritonProductConfig.daypart_ids instead"
        )

    return unsupported
