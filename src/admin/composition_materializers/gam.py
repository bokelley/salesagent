"""GAM materializer for storefront-composed products.

Produces an ``implementation_config`` dict shaped for the GAM adapter
(``src/adapters/gam/managers/orders.py``). Reads the operator-authored
``inventory_profile.inventory_config`` for the where, and resolves each
``SignalSelection`` through the corresponding ``TenantSignal.adapter_config``
for the targeting overlay.

Output schema (matches what the GAM orders manager consumes today):

  {
    "targeted_ad_unit_ids": [...],
    "targeted_placement_ids": [...],
    "include_descendants": bool,
    "custom_targeting_keys": {
        key_id: {"values": [value_ids], "operator": "IS" | "IS_NOT"}
    },
    "audience_segment_ids": [...],
    "excluded_audience_segment_ids": [...],
  }

The ``custom_targeting_keys`` shape feeds directly into
``_build_custom_criteria_set`` (``src/adapters/gam/managers/orders.py:53``)
unchanged — verified end-to-end.
"""

from __future__ import annotations

from typing import Any

from src.admin.api_schemas.composition import CompositionErrorDetail
from src.admin.composition_materializers import (
    MaterializationError,
    MaterializerContext,
    register,
)


class GamMaterializer:
    adapter_key = "google_ad_manager"

    def materialize(self, ctx: MaterializerContext) -> dict[str, Any]:
        inv = ctx.inventory_profile.inventory_config or {}
        config: dict[str, Any] = {
            "targeted_ad_unit_ids": list(inv.get("ad_units", []) or []),
            "targeted_placement_ids": list(inv.get("placements", []) or []),
            "include_descendants": bool(inv.get("include_descendants", True)),
        }

        custom_targeting_keys: dict[str, dict[str, Any]] = {}
        audience_include: list[str] = []
        audience_exclude: list[str] = []
        errors: list[CompositionErrorDetail] = []

        for selection in ctx.signal_selections:
            signal = ctx.signals_by_id.get(selection.signal_id)
            if signal is None:
                # Unknown-signal errors are caught earlier in the route, but
                # defend in depth.
                errors.append(
                    CompositionErrorDetail(
                        code="unknown_signal",
                        signal_id=selection.signal_id,
                        message=f"Signal {selection.signal_id!r} not declared on tenant.",
                    )
                )
                continue

            adapter_cfg = signal.adapter_config or {}
            kind = adapter_cfg.get("kind")
            gam_operator = "IS" if selection.mode == "include" else "IS_NOT"

            if kind == "custom_key_value":
                key_id = adapter_cfg.get("key_id")
                value_ids_map: dict[str, str] = adapter_cfg.get("value_ids") or {}
                if not key_id:
                    errors.append(
                        CompositionErrorDetail(
                            code="invalid_signal_selection",
                            signal_id=signal.signal_id,
                            adapter="google_ad_manager",
                            message=f"Signal {signal.signal_id!r} adapter_config missing 'key_id'.",
                        )
                    )
                    continue
                resolved_value_ids = _resolve_value_ids(
                    selection=selection,
                    signal=signal,
                    value_ids_map=value_ids_map,
                    errors=errors,
                )
                if resolved_value_ids:
                    slot = custom_targeting_keys.setdefault(str(key_id), {"values": [], "operator": gam_operator})
                    slot["operator"] = gam_operator
                    slot["values"].extend(resolved_value_ids)

            elif kind == "audience_segment":
                segment_id = adapter_cfg.get("segment_id")
                if not segment_id:
                    errors.append(
                        CompositionErrorDetail(
                            code="invalid_signal_selection",
                            signal_id=signal.signal_id,
                            adapter="google_ad_manager",
                            message=f"Signal {signal.signal_id!r} adapter_config missing 'segment_id'.",
                        )
                    )
                    continue
                (audience_include if selection.mode == "include" else audience_exclude).append(str(segment_id))

            else:
                errors.append(
                    CompositionErrorDetail(
                        code="invalid_signal_selection",
                        signal_id=signal.signal_id,
                        adapter="google_ad_manager",
                        message=(
                            f"Signal {signal.signal_id!r} has adapter_config.kind={kind!r}, "
                            "which the GAM materializer does not recognize "
                            "(expected 'custom_key_value' or 'audience_segment')."
                        ),
                    )
                )

        if errors:
            raise MaterializationError(errors)

        if custom_targeting_keys:
            config["custom_targeting_keys"] = custom_targeting_keys
        if audience_include:
            config["audience_segment_ids"] = audience_include
        if audience_exclude:
            config["excluded_audience_segment_ids"] = audience_exclude

        return config


def _resolve_value_ids(
    *,
    selection,
    signal,
    value_ids_map: dict[str, str],
    errors: list[CompositionErrorDetail],
) -> list[str]:
    """Map storefront-supplied human values to GAM value-ids via the signal's
    ``adapter_config.value_ids`` mapping. Unmapped values surface as a
    structured error so the storefront can correct.
    """
    if signal.value_type != "categorical":
        # For binary / numeric signals, the storefront selection is the
        # value itself (boolean toggle, range bounds). GAM custom-KVs are
        # fundamentally categorical, so this should not happen in practice;
        # treat as misconfiguration.
        errors.append(
            CompositionErrorDetail(
                code="invalid_signal_selection",
                signal_id=signal.signal_id,
                adapter="google_ad_manager",
                message=(f"GAM custom_key_value signals require value_type='categorical'; got {signal.value_type!r}."),
            )
        )
        return []
    resolved: list[str] = []
    for v in selection.values:
        mapped = value_ids_map.get(v)
        if mapped is None:
            errors.append(
                CompositionErrorDetail(
                    code="invalid_signal_selection",
                    signal_id=signal.signal_id,
                    adapter="google_ad_manager",
                    got=v,
                    message=f"Value {v!r} not in signal's adapter_config.value_ids mapping.",
                )
            )
            continue
        resolved.append(str(mapped))
    return resolved


register(GamMaterializer())
