"""Unit tests for the bundle-inventory adapter registry (#521).

Exercises the registry + protocol + stub adapters. The GAM adapter's
SQL-backed methods are covered by integration tests in
``tests/integration/test_inventory_profiles_list_redesign.py``.
"""

from __future__ import annotations

import pytest

from src.services.bundle_adapter import (
    BundleInventoryAdapter,
    BundleInventoryRow,
    adapter_for_tenant,
    get_adapter,
    iter_adapters,
)


class TestRegistry:
    """Registry resolution + tenant matching."""

    def test_three_adapters_registered_at_import(self):
        ids = {a.adapter_id for a in iter_adapters()}
        assert ids == {"gam", "freewheel", "springserve"}

    def test_get_adapter_returns_registered_instance(self):
        gam = get_adapter("gam")
        assert gam is not None
        assert gam.adapter_id == "gam"
        assert gam.label == "Google Ad Manager"

    def test_get_adapter_unknown_returns_none(self):
        assert get_adapter("xandr") is None

    @pytest.mark.parametrize(
        "ad_server,expected",
        [
            ("google_ad_manager", "gam"),
            ("gam", "gam"),
            ("freewheel", "freewheel"),
            ("fw", "freewheel"),
            ("springserve", "springserve"),
            ("ss", "springserve"),
        ],
    )
    def test_adapter_for_tenant_matches_ad_server_aliases(self, ad_server, expected):
        adapter = adapter_for_tenant(ad_server)
        assert adapter is not None
        assert adapter.adapter_id == expected

    def test_adapter_for_tenant_unknown_returns_none(self):
        assert adapter_for_tenant("xandr") is None
        assert adapter_for_tenant(None) is None
        assert adapter_for_tenant("") is None


class TestRowShape:
    """``BundleInventoryRow`` is the wire format between adapters and UI."""

    def test_row_carries_external_id_name_entity_meta(self):
        row = BundleInventoryRow(
            external_id="14512330",
            name="Homepage Premium",
            entity_type="placement",
            meta="6 ad units · synced 3d ago",
        )
        assert row.external_id == "14512330"
        assert row.name == "Homepage Premium"
        assert row.entity_type == "placement"
        assert row.meta == "6 ad units · synced 3d ago"
        # raw defaults to empty so adapters that don't carry extras don't need to.
        assert row.raw == {}


class TestStubAdapters:
    """FreeWheel + SpringServe stubs return empty results but keep vocab."""

    def test_freewheel_stub_has_correct_label_and_vocab(self):
        fw = get_adapter("freewheel")
        assert fw is not None
        assert fw.label == "FreeWheel"
        # FW's "placements" is the primary leaf entity; "placement groups" the wrapper.
        assert fw.vocab == {"primary": "placements", "secondary": "placement groups"}

    def test_springserve_stub_has_correct_label_and_vocab(self):
        ss = get_adapter("springserve")
        assert ss is not None
        assert ss.label == "SpringServe"
        assert ss.vocab == {"primary": "tags", "secondary": "demand tags"}

    def test_stub_methods_return_empty_safely(self):
        """All read methods should return empty containers, not raise.

        Lets the blueprint code path that calls the adapter degrade
        cleanly for tenants on adapters whose sync surfaces aren't wired.
        """
        fw = get_adapter("freewheel")
        # session/tenant_id are unused by the stub but match the protocol.
        assert fw.has_synced_inventory(None, "t1") is False
        assert fw.count_inventory(None, "t1", "ad_unit") == 0
        assert fw.list_inventory_by_ids(None, "t1", "ad_unit", ["a", "b"]) == []
        assert fw.list_unbundled(None, "t1", {"ad_unit": set(), "placement": set()}, 10) == []
        assert fw.list_top_level_placements(None, "t1", 5) == []
        assert fw.find_inventory_item(None, "t1", "ad_unit", "missing") is None


class TestProtocolConformance:
    """Every registered adapter must satisfy the runtime-checked Protocol."""

    @pytest.mark.parametrize("adapter_id", ["gam", "freewheel", "springserve"])
    def test_adapter_isinstance_protocol(self, adapter_id):
        adapter = get_adapter(adapter_id)
        assert isinstance(adapter, BundleInventoryAdapter)
