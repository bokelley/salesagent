"""Tests for the uniform adapter sync contract on AdServerAdapter.

The contract is what the shared :class:`AdapterSyncScheduler` (PR #382)
will call on every adapter. Each adapter declares ``supports_*_sync``
flags on :class:`AdapterCapabilities` and overrides the matching
``run_*_sync()`` method; freshness accessors let UI surface staleness
without per-adapter endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.adapters.base import (
    AdapterCapabilities,
    AdapterSyncResult,
    AdServerAdapter,
)


class TestAdapterSyncResult:
    """The wire shape every adapter returns from run_*_sync."""

    def test_total_count_sums_per_kind_counts(self):
        result = AdapterSyncResult(
            sync_kind="inventory",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            succeeded=True,
            counts={"site": 10, "site_section": 50},
        )
        assert result.total_count == 60

    def test_empty_counts_total_zero(self):
        result = AdapterSyncResult(
            sync_kind="reporting",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            succeeded=False,
        )
        assert result.total_count == 0

    def test_metadata_freely_typed(self):
        """metadata is a free-form dict so reporting can stash job_id +
        inventory can stash whatever cache stats it wants — UI displays
        without the scheduler needing to know the schema."""
        result = AdapterSyncResult(
            sync_kind="reporting",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            succeeded=True,
            metadata={"job_id": "abc123", "placements_updated": 42},
        )
        assert result.metadata["job_id"] == "abc123"


class TestAdapterCapabilitiesFlags:
    """The two capability flags the AdapterSyncScheduler reads."""

    def test_both_flags_default_off(self):
        """New adapter classes shouldn't accidentally opt in — flags are
        false by default; adapters declare true explicitly."""
        caps = AdapterCapabilities()
        assert caps.supports_inventory_sync is False
        assert caps.supports_reporting_sync is False

    def test_flags_independent(self):
        """Inventory sync and reporting sync are independent capabilities —
        an adapter can support one without the other (Triton supports
        inventory but not reporting; Mock supports neither)."""
        caps = AdapterCapabilities(supports_inventory_sync=True)
        assert caps.supports_inventory_sync is True
        assert caps.supports_reporting_sync is False


class TestDefaultContractImplementation:
    """An adapter that doesn't override the sync methods must raise
    NotImplementedError with a message that tells the operator how to fix
    it — flip the capability flag off, OR override the method."""

    def _bare_adapter(self):
        """Build a minimal AdServerAdapter subclass that doesn't override
        run_*_sync, so we hit the base-class defaults."""

        class _BareAdapter(AdServerAdapter):
            adapter_name = "bare"
            capabilities = AdapterCapabilities()

            def create_media_buy(self, *a, **kw):  # type: ignore[override]
                pass

            def add_creative_assets(self, *a, **kw):  # type: ignore[override]
                return []

            def associate_creatives(self, *a, **kw):  # type: ignore[override]
                return []

            def check_media_buy_status(self, *a, **kw):  # type: ignore[override]
                pass

            def get_media_buy_delivery(self, *a, **kw):  # type: ignore[override]
                pass

            def update_media_buy(self, *a, **kw):  # type: ignore[override]
                pass

            def process_assets(self, *a, **kw):  # type: ignore[override]
                return []

        principal = MagicMock()
        principal.principal_id = "p1"
        principal.platform_mappings = {"bare": {}}
        return _BareAdapter(config={"enabled": True}, principal=principal, dry_run=True, tenant_id="t1")

    def test_run_inventory_sync_raises_with_actionable_message(self):
        """The error message points the operator at the fix — flip the
        capability flag or implement the method."""
        adapter = self._bare_adapter()
        with pytest.raises(NotImplementedError) as exc:
            adapter.run_inventory_sync()
        message = str(exc.value)
        assert "supports_inventory_sync" in message
        assert "run_inventory_sync" in message

    def test_run_reporting_sync_raises_with_actionable_message(self):
        adapter = self._bare_adapter()
        with pytest.raises(NotImplementedError) as exc:
            adapter.run_reporting_sync()
        message = str(exc.value)
        assert "supports_reporting_sync" in message
        assert "run_reporting_sync" in message

    def test_latest_sync_accessors_return_none_by_default(self):
        """Adapters without caches return None; the scheduling UI shows
        "Never synced" rather than crashing on absent state."""
        adapter = self._bare_adapter()
        assert adapter.latest_inventory_sync_at() is None
        assert adapter.latest_reporting_sync_at() is None


class TestContractOverrideAcceptsAdapterSyncResult:
    """An adapter that overrides run_*_sync must return AdapterSyncResult.
    The shared scheduler relies on this — these tests make the contract
    explicit so a future adapter author hitting the test suite knows
    immediately what shape to return."""

    def test_inventory_override_returning_adapter_sync_result_is_accepted(self):
        from datetime import timedelta

        class _GoodAdapter(AdServerAdapter):
            adapter_name = "good"
            capabilities = AdapterCapabilities(supports_inventory_sync=True)

            def create_media_buy(self, *a, **kw):  # type: ignore[override]
                pass

            def add_creative_assets(self, *a, **kw):  # type: ignore[override]
                return []

            def associate_creatives(self, *a, **kw):  # type: ignore[override]
                return []

            def check_media_buy_status(self, *a, **kw):  # type: ignore[override]
                pass

            def get_media_buy_delivery(self, *a, **kw):  # type: ignore[override]
                pass

            def update_media_buy(self, *a, **kw):  # type: ignore[override]
                pass

            def process_assets(self, *a, **kw):  # type: ignore[override]
                return []

            def run_inventory_sync(self) -> AdapterSyncResult:  # type: ignore[override]
                start = datetime.now(UTC)
                return AdapterSyncResult(
                    sync_kind="inventory",
                    started_at=start,
                    finished_at=start + timedelta(seconds=5),
                    succeeded=True,
                    counts={"placement": 100},
                )

        principal = MagicMock()
        principal.principal_id = "p1"
        principal.platform_mappings = {"good": {}}
        adapter = _GoodAdapter(config={"enabled": True}, principal=principal, dry_run=True, tenant_id="t1")

        result = adapter.run_inventory_sync()
        assert isinstance(result, AdapterSyncResult)
        assert result.sync_kind == "inventory"
        assert result.total_count == 100
        assert result.succeeded
