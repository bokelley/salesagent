"""SpringServe materializer (placeholder, namespaced passthrough).

SpringServe (PR #427 on main; CTV/OLV/audio inventory) doesn't yet have
storefront-driven signal resolution wired up. We emit the operator-authored
``inventory_profile.inventory_config`` verbatim under a ``"springserve"``
namespace, plus a structured trail of each signal selection so the adapter
or an operator translator can consume them when SpringServe-side signal
mapping lands.
"""

from __future__ import annotations

from typing import Any

from src.admin.composition_materializers import MaterializerContext, register


class SpringServeMaterializer:
    adapter_key = "springserve"

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
                    "range": selection.range.model_dump(mode="json") if selection.range else None,
                    "adapter_config": signal.adapter_config or {},
                }
            )
        return {
            "springserve": {
                **inv,
                "signals": signal_entries,
            }
        }


register(SpringServeMaterializer())
