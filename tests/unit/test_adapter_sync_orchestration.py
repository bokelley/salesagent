"""Unit tests for the shared adapter sync orchestration (#382 Stage 3).

DB-touching tests (SyncJob persistence) live in
``tests/integration/test_adapter_sync_orchestration.py``. This file
covers the contract corners that don't require the database.
"""

from __future__ import annotations

import pytest

from src.services.adapter_sync_orchestration import (
    KIND_INVENTORY,
    KIND_REPORTING,
    AdapterDoesNotSupportSyncKind,
    execute_sync,
)
from tests.helpers.sync_orchestration import make_mock_adapter as _mock_adapter


class TestCapabilityGating:
    """execute_sync rejects sync_kinds the adapter hasn't declared
    support for — fail fast at the orchestration boundary rather than
    inside the adapter."""

    def test_inventory_sync_rejected_when_capability_off(self):
        adapter = _mock_adapter(supports_inventory=False)
        with pytest.raises(AdapterDoesNotSupportSyncKind) as exc:
            execute_sync(adapter=adapter, tenant_id="t1", sync_kind=KIND_INVENTORY, triggered_by="test")
        assert "supports_inventory_sync" in str(exc.value)
        adapter.run_inventory_sync.assert_not_called()

    def test_reporting_sync_rejected_when_capability_off(self):
        adapter = _mock_adapter(supports_reporting=False)
        with pytest.raises(AdapterDoesNotSupportSyncKind):
            execute_sync(adapter=adapter, tenant_id="t1", sync_kind=KIND_REPORTING, triggered_by="test")

    def test_unknown_sync_kind_rejected_with_valueerror(self):
        adapter = _mock_adapter(supports_inventory=True)
        with pytest.raises(ValueError, match="sync_kind"):
            execute_sync(adapter=adapter, tenant_id="t1", sync_kind="foobar", triggered_by="test")
