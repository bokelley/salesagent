"""Mock materializer.

The mock adapter doesn't consume specific ``implementation_config`` fields
— it records media buys for testing without translating to ad-server
primitives. The materializer therefore preserves a faithful record of
what was composed (helpful for tests + debugging) and passes it through.
"""

from __future__ import annotations

from typing import Any

from src.admin.composition_materializers import MaterializerContext, register


class MockMaterializer:
    adapter_key = "mock"

    def materialize(self, ctx: MaterializerContext) -> dict[str, Any]:
        inv = ctx.inventory_profile.inventory_config or {}
        return {
            "inventory_profile_id": ctx.inventory_profile.profile_id,
            "inventory_config": dict(inv),
            "signal_selections": [s.model_dump(mode="json") for s in ctx.signal_selections],
        }


register(MockMaterializer())
