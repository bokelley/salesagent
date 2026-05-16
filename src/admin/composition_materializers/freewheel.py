"""Freewheel materializer (placeholder, namespaced passthrough).

The Freewheel adapter (``src/adapters/freewheel/adapter.py``) reads its
configuration from ``impl.get("freewheel", impl)`` — it looks in a
``"freewheel"`` namespace first and falls back to the top level. We use
that contract: emit the operator-authored ``inventory_profile.inventory_config``
verbatim under ``"freewheel"``, plus a structured trail of each signal
selection's ``adapter_config`` so the adapter can consume them once
Freewheel-side signal resolution lands.

This is a minimum-viable wire path. Full Freewheel signal support
(audience_items / viewership_profiles / targeting_profile_id translation
from ``SignalSelection``) is a follow-up — see ``.context/embedded-composition-design.md``.
"""

from __future__ import annotations

from typing import Any

from src.admin.composition_materializers import MaterializerContext, register


class FreewheelMaterializer:
    adapter_key = "freewheel"

    def materialize(self, ctx: MaterializerContext) -> dict[str, Any]:
        inv = dict(ctx.inventory_profile.inventory_config or {})

        # Carry resolved signal adapter_config alongside selection mode so
        # the adapter can fold them into its targeting block once Freewheel
        # signal mapping is wired up.
        signal_entries: list[dict[str, Any]] = []
        for selection in ctx.signal_selections:
            signal = ctx.signals_by_id.get(selection.signal_id)
            if signal is None:
                continue
            signal_entries.append(
                {
                    "signal_id": signal.signal_id,
                    "mode": selection.mode,
                    "values": list(selection.values),
                    "range": selection.range.model_dump(mode="json") if selection.range else None,
                    "adapter_config": signal.adapter_config or {},
                }
            )

        return {
            "freewheel": {
                **inv,
                "signals": signal_entries,
            }
        }


register(FreewheelMaterializer())
