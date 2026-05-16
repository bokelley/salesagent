"""Broadstreet materializer (placeholder, namespaced passthrough).

Broadstreet's only inventory primitive is zones; it explicitly declares
``supports_custom_targeting=False``. Storefronts composing against
Broadstreet tenants will typically pass no signal selections; if they do,
each selection's ``adapter_config`` is preserved as a trail (the adapter
or an operator-side translator could later consume it).

Emits config under a ``"broadstreet"`` namespace mirroring the namespace
pattern other adapters in this codebase use.
"""

from __future__ import annotations

from typing import Any

from src.admin.composition_materializers import MaterializerContext, register


class BroadstreetMaterializer:
    adapter_key = "broadstreet"

    def materialize(self, ctx: MaterializerContext) -> dict[str, Any]:
        inv = dict(ctx.inventory_profile.inventory_config or {})
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
                    "adapter_config": signal.adapter_config or {},
                }
            )
        return {
            "broadstreet": {
                **inv,
                "signals": signal_entries,
            }
        }


register(BroadstreetMaterializer())
