"""Per-adapter materializers for storefront-composed products.

Each adapter consumes a different ``implementation_config`` shape — GAM
expects ``targeted_ad_unit_ids`` / ``custom_targeting_keys``; Freewheel
expects ``site_ids`` / ``audience_item_ids`` / ``targeting_profile_id``;
Broadstreet expects ``targeted_zone_ids``; SpringServe TBD.

The composition API is adapter-agnostic at the storefront boundary: the
storefront passes ``inventory_profile_id`` + ``signal_selections`` + price
+ dates. At compose time, this module dispatches on ``tenant.ad_server``
and the matching materializer:

  1. Takes the operator-authored ``inventory_profile.inventory_config``
     (already in the adapter's vocabulary).
  2. Resolves each ``signal_selection`` through the corresponding
     ``TenantSignal.adapter_config`` to adapter-specific targeting fields.
  3. Merges into a single ``implementation_config`` dict the adapter
     reads at line-item / order creation.

Materializers that need to indicate the adapter doesn't support a given
selection raise :class:`MaterializationError` with structured detail; the
composition endpoint surfaces it as a 422 ``composition_invalid``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from src.admin.api_schemas.composition import (
    CompositionErrorDetail,
    SignalSelection,
)
from src.core.database.models import InventoryProfile, TenantSignal


class MaterializationError(Exception):
    """Raised by a materializer when a composition is not realizable.

    Carries structured :class:`CompositionErrorDetail` entries so the
    composition endpoint can surface them as 422 details directly.
    """

    def __init__(self, details: Sequence[CompositionErrorDetail]) -> None:
        self.details: list[CompositionErrorDetail] = list(details)
        super().__init__(f"{len(self.details)} materialization error(s)")


@dataclass
class MaterializerContext:
    inventory_profile: InventoryProfile
    signal_selections: list[SignalSelection]
    signals_by_id: dict[str, TenantSignal]
    adcp_targeting: dict[str, Any] | None


class Materializer(Protocol):
    """Produce an ``implementation_config`` dict the adapter will consume."""

    adapter_key: str

    def materialize(self, ctx: MaterializerContext) -> dict[str, Any]: ...


_REGISTRY: dict[str, Materializer] = {}


def register(materializer: Materializer) -> Materializer:
    _REGISTRY[materializer.adapter_key] = materializer
    return materializer


def get(adapter_key: str) -> Materializer | None:
    return _REGISTRY.get(adapter_key)


def supported_adapters() -> list[str]:
    return sorted(_REGISTRY.keys())


# Side-effect imports register each adapter's materializer.
from src.admin.composition_materializers import broadstreet as _broadstreet  # noqa: E402, F401
from src.admin.composition_materializers import freewheel as _freewheel  # noqa: E402, F401
from src.admin.composition_materializers import gam as _gam  # noqa: E402, F401
from src.admin.composition_materializers import mock as _mock  # noqa: E402, F401
from src.admin.composition_materializers import springserve as _springserve  # noqa: E402, F401
