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
    inventory_result=None,
    reporting_result=None,
):
    """Stripped-down ``AdServerAdapter`` exposing only what
    :func:`execute_sync` needs: ``capabilities`` + ``run_*_sync``
    methods. Caller can pre-program return values for both methods."""
    adapter = MagicMock(spec=AdServerAdapter)
    adapter.__class__ = type(
        "_MockAdapter",
        (AdServerAdapter,),
        {"adapter_name": "_mock_test"},
    )
    adapter.capabilities = AdapterCapabilities(
        supports_inventory_sync=supports_inventory,
        supports_reporting_sync=supports_reporting,
    )
    adapter.run_inventory_sync = MagicMock(return_value=inventory_result)
    adapter.run_reporting_sync = MagicMock(return_value=reporting_result)
    return adapter
