"""Broadstreet capability flags must match what the adapter actually implements.

A capability declared True without a matching implementation crashes the
worker path that calls it. The shared refresh / sync scheduler reads
``capabilities.supports_inventory_sync`` to decide whether to enqueue an
inventory sync row — if the flag is True but ``run_inventory_sync()`` is
not overridden, the row sits permanently failed on the publisher's
dashboard with a NotImplementedError.

This test pins the contract: capability flags must be honest. When
``run_inventory_sync`` lands for Broadstreet, flip the flag back to True
in the same change.
"""

from __future__ import annotations

import pytest

from src.adapters.base import AdServerAdapter
from src.adapters.broadstreet import BroadstreetAdapter


class TestBroadstreetCapabilityHonesty:
    def test_inventory_sync_capability_matches_implementation(self):
        """If supports_inventory_sync is True, run_inventory_sync MUST be
        overridden — anything else crashes the sync worker."""
        declares_sync = BroadstreetAdapter.capabilities.supports_inventory_sync
        # Method must be overridden from base to be considered implemented.
        implements_sync = BroadstreetAdapter.run_inventory_sync is not AdServerAdapter.run_inventory_sync

        if declares_sync:
            assert implements_sync, (
                "BroadstreetAdapter declares supports_inventory_sync=True but does not override "
                "run_inventory_sync(). Either implement it or flip the capability to False."
            )
        # The converse (implements but doesn't declare) is allowed — capability
        # flags gate scheduler behavior, and an unused implementation is harmless.

    def test_inventory_sync_currently_disabled(self):
        """Sentinel test: documents the current state. When inventory sync
        is implemented for Broadstreet (issue #448), flip this assertion
        and the test above will catch any regression."""
        assert BroadstreetAdapter.capabilities.supports_inventory_sync is False, (
            "Broadstreet inventory sync was implemented — update this test and remove the "
            "FIXME in the adapter's capabilities block."
        )

    def test_run_inventory_sync_raises_if_called_directly(self):
        """If something bypasses the capability check and calls
        run_inventory_sync directly, it must raise NotImplementedError
        (the base class default) so the worker thread surfaces the
        failure cleanly via _mark_sync_failed_on_spawn."""
        from src.core.schemas import Principal

        principal = Principal(
            principal_id="p1",
            name="Test",
            platform_mappings={"broadstreet": {"advertiser_id": "1"}},
        )
        adapter = BroadstreetAdapter(
            config={"network_id": "nw1", "api_key": "k", "default_advertiser_id": "1"},
            principal=principal,
            dry_run=True,
            tenant_id="t1",
        )
        with pytest.raises(NotImplementedError, match="run_inventory_sync"):
            adapter.run_inventory_sync()
