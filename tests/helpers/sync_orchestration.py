"""Shared helpers for adapter sync orchestration tests.

The mock-adapter construction is identical between the unit + integration
test files, so the dup-guard flagged it (CLAUDE.md DRY invariant). Extract
the common factory here so both files reuse it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.adapters.base import AdapterCapabilities, AdServerAdapter


def make_mock_adapter(
    *,
    supports_inventory: bool = False,
    supports_reporting: bool = False,
    supports_price_guidance: bool = False,
    supports_availability_guidance: bool = False,
    supports_signal_coverage: bool = False,
    inventory_result=None,
    reporting_result=None,
    price_guidance_result=None,
    availability_guidance_result=None,
    signal_coverage_result=None,
    adapter_name: str = "_mock_test",
):
    """Stripped-down ``AdServerAdapter`` exposing only what
    :func:`execute_sync` needs: ``capabilities`` + ``run_*_sync``
    methods. Caller can pre-program return values for both methods.

    ``adapter_name`` flows through to ``SyncJob.adapter_type`` so tests
    asserting against the scheduling matrix (#382 Stage 4) can seed rows
    with the real adapter_type keys (``freewheel`` / ``google_ad_manager``)."""
    adapter = MagicMock(spec=AdServerAdapter)
    adapter.__class__ = type(
        "_MockAdapter",
        (AdServerAdapter,),
        {"adapter_name": adapter_name},
    )
    adapter.capabilities = AdapterCapabilities(
        supports_inventory_sync=supports_inventory,
        supports_reporting_sync=supports_reporting,
        supports_price_guidance_sync=supports_price_guidance,
        supports_availability_guidance_sync=supports_availability_guidance,
        supports_signal_coverage_sync=supports_signal_coverage,
    )
    adapter.run_inventory_sync = MagicMock(return_value=inventory_result)
    adapter.run_reporting_sync = MagicMock(return_value=reporting_result)
    adapter.run_price_guidance_sync = MagicMock(return_value=price_guidance_result)
    adapter.run_availability_guidance_sync = MagicMock(return_value=availability_guidance_result)
    adapter.run_signal_coverage_sync = MagicMock(return_value=signal_coverage_result)
    return adapter
