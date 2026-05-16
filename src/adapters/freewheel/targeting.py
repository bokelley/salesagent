"""Translate AdCP targeting into FreeWheel line-item targeting.

FreeWheel models targeting as a structured object on the line item with
``geo``, ``device``, ``customCriteria``, and a reference to a pre-built
``targetingProfileId``. Multiple criteria combine with AND.

The exact wire format is finalised against staging credentials — this
module emits the canonical shape documented in the Publisher API reference
and is exercised by dry-run logging until live calls land.

Signal resolution: buyer-supplied ``audience_include`` references resolve
through operator-declared ``TenantSignal`` rows whose ``adapter_config``
carries FW-specific kinds (``freewheel_viewership_profile``,
``freewheel_audience_item``, ``freewheel_custom_kv``). FW's flat-AND
targeting model means signal contributions layer directly onto the
existing ``viewershipProfileIds`` / ``audienceItemIds`` / ``customCriteria``
fields. FW has no native exclusion semantic for these fields, so
``audience_exclude`` references are rejected with an
``unsupported_targeting`` error.
"""

from __future__ import annotations

from typing import Any


def build_targeting(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build the FreeWheel ``targeting`` object for a line item.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model (geo, device, custom).
        product_config: ``FreeWheelProductConfig`` as a dict — supplies
            ``targeting_profile_id`` and product-default ``custom_targeting``.
        tenant_id: When provided, ``audience_include`` references on the
            overlay are resolved through the tenant's ``tenant_signals``
            table. Required for signal resolution; if omitted, signals are
            ignored (preserves the existing ``audiences_any_of`` rejection
            in ``validate_targeting``).
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

    # Resolve operator-declared signals referenced in audience_include.
    # FW has no native exclusion → audience_exclude with declared signals is
    # rejected in validate_targeting. Anything that lands here is include.
    if tenant_id and targeting_overlay is not None and getattr(targeting_overlay, "audience_include", None):
        viewership_profile_ids: list[int] = []
        audience_item_ids: list[int] = []
        _resolve_audience_signals(
            tenant_id=tenant_id,
            signal_ids=list(targeting_overlay.audience_include or []),
            viewership_profile_ids=viewership_profile_ids,
            audience_item_ids=audience_item_ids,
            custom_targeting=custom,
        )
        if viewership_profile_ids:
            targeting["viewershipProfileIds"] = viewership_profile_ids
        if audience_item_ids:
            targeting["audienceItemIds"] = audience_item_ids

    if custom:
        targeting["customCriteria"] = [{"key": k, "values": v} for k, v in custom.items()]

    return targeting


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for FreeWheel.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. Frequency cap and
    dayparting overlays are rejected pending sandbox-validated translation
    to FreeWheel's native shapes — until the Publisher API JSON contract is
    locked in (see docs/adapters/freewheel/README.md), passing them through
    would risk shipping the wrong wire format.

    ``audience_include`` is allowed when it references operator-declared
    ``TenantSignal`` rows (resolved in ``build_targeting``). ``audience_exclude``
    is rejected — FW's targeting model has no per-field exclusion for
    audiences / viewership profiles / custom KV.
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

    if getattr(targeting_overlay, "audience_exclude", None):
        unsupported.append(
            "Audience exclusion is not supported on FreeWheel — its targeting model has no "
            "per-field exclusion for viewership profiles / audience items / custom KV. Use "
            "an include-only signal that pre-excludes the unwanted segments."
        )

    if getattr(targeting_overlay, "dayparting", None):
        unsupported.append(
            "Free-form dayparting pending FreeWheel sandbox validation — "
            "use a pre-built FreeWheel targeting profile via FreeWheelProductConfig.targeting_profile_id"
        )

    return unsupported


# ---------------------------------------------------------------------------
# Signal resolution (FreeWheel)
# ---------------------------------------------------------------------------


def _resolve_audience_signals(
    *,
    tenant_id: str,
    signal_ids: list[str],
    viewership_profile_ids: list[int],
    audience_item_ids: list[int],
    custom_targeting: dict[str, list[str]],
) -> None:
    """Look up each signal_id in ``tenant_signals`` and contribute its
    resolved criteria to the right FW targeting accumulator.

    Same pattern as the GAM materializer: pass-through and composed shapes
    both produce a list of atomic criteria; each criterion's ``kind``
    dispatches to the right FW field. FW has no exclusion semantics, so
    criteria with ``mode='exclude'`` raise — operators author exclude
    semantics by NOT including the segment.
    """
    from src.core.database.repositories.uow import TenantSignalUoW

    with TenantSignalUoW(tenant_id) as uow:
        assert uow.tenant_signals is not None
        signals_by_id = {s.signal_id: s for s in uow.tenant_signals.list_by_ids(signal_ids)}

        missing = [sid for sid in signal_ids if sid not in signals_by_id]
        if missing:
            raise ValueError(
                f"FW audience targeting references signal(s) not declared on tenant "
                f"{tenant_id!r}: {', '.join(sorted(missing))}. "
                f"Author each signal via POST /api/v1/tenants/<id>/signals first."
            )

        for signal_id in signal_ids:
            signal = signals_by_id[signal_id]
            for criterion in _signal_criteria(signal):
                if criterion["mode"] == "exclude":
                    raise ValueError(
                        f"FW signal {signal.signal_id!r} criterion mode='exclude' is not "
                        f"supported by FreeWheel — its targeting model has no per-field "
                        f"exclusion. Author an include-only signal that already pre-excludes."
                    )
                _apply_criterion(
                    signal=signal,
                    criterion=criterion,
                    viewership_profile_ids=viewership_profile_ids,
                    audience_item_ids=audience_item_ids,
                    custom_targeting=custom_targeting,
                )


def _signal_criteria(signal) -> list[dict[str, Any]]:
    """Normalize ``TenantSignal.adapter_config`` to a list of validated
    criterion dicts. Pass-through and composed both produce the same shape;
    legacy rows without ``type`` infer pass-through.
    """
    cfg = signal.adapter_config or {}
    config_type = cfg.get("type")

    if config_type == "composed":
        raw_criteria = cfg.get("criteria") or []
        if not isinstance(raw_criteria, list):
            raise ValueError(
                f"Signal {signal.signal_id!r} type='composed' requires criteria: list, "
                f"got {type(raw_criteria).__name__}."
            )
        return [_validate_criterion(signal, c) for c in raw_criteria]

    kind = cfg.get("kind")
    if kind in ("freewheel_viewership_profile", "freewheel_audience_item", "freewheel_custom_kv"):
        return [_validate_criterion(signal, {**cfg, "mode": cfg.get("mode", "include")})]

    raise ValueError(
        f"Signal {signal.signal_id!r} adapter_config must declare type='passthrough' "
        f"(with FW kind) or type='composed' (with criteria). Got "
        f"type={config_type!r}, kind={kind!r}. Expected kinds: "
        f"freewheel_viewership_profile, freewheel_audience_item, freewheel_custom_kv."
    )


def _validate_criterion(signal, criterion: dict[str, Any]) -> dict[str, Any]:
    """Validate one FW criterion. Returns normalized dict."""
    kind = criterion.get("kind")
    mode = criterion.get("mode", "include")
    if mode not in ("include", "exclude"):
        raise ValueError(f"Signal {signal.signal_id!r} criterion has mode={mode!r}; expected include or exclude.")
    if kind == "freewheel_viewership_profile":
        if not criterion.get("profile_id"):
            raise ValueError(
                f"Signal {signal.signal_id!r} criterion kind='freewheel_viewership_profile' requires profile_id."
            )
    elif kind == "freewheel_audience_item":
        if not criterion.get("item_id"):
            raise ValueError(f"Signal {signal.signal_id!r} criterion kind='freewheel_audience_item' requires item_id.")
    elif kind == "freewheel_custom_kv":
        if not criterion.get("key") or not criterion.get("value_id"):
            raise ValueError(
                f"Signal {signal.signal_id!r} criterion kind='freewheel_custom_kv' requires key and value_id."
            )
    else:
        raise ValueError(
            f"Signal {signal.signal_id!r} criterion has unknown kind={kind!r} "
            f"(expected freewheel_viewership_profile, freewheel_audience_item, freewheel_custom_kv)."
        )
    return {**criterion, "kind": kind, "mode": mode}


def _apply_criterion(
    *,
    signal,
    criterion: dict[str, Any],
    viewership_profile_ids: list[int],
    audience_item_ids: list[int],
    custom_targeting: dict[str, list[str]],
) -> None:
    """Contribute one validated criterion to the right FW accumulator."""
    kind = criterion["kind"]
    if kind == "freewheel_viewership_profile":
        viewership_profile_ids.append(int(criterion["profile_id"]))
    elif kind == "freewheel_audience_item":
        audience_item_ids.append(int(criterion["item_id"]))
    elif kind == "freewheel_custom_kv":
        key = criterion["key"]
        # FW customCriteria is OR-within-key (multi-value list). Append
        # this value to the key's bucket; multiple criteria on the same
        # key from different signals naturally OR together.
        custom_targeting.setdefault(key, []).append(str(criterion["value_id"]))
    else:  # pragma: no cover — validated above
        raise ValueError(f"Signal {signal.signal_id!r} unsupported kind {kind!r}")
