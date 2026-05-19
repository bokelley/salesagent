"""Tests for SpringServe targeting translation -- AdCP overlay -> demand-tag fields."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.springserve.targeting import (
    build_demand_tag_kv_entries,
    build_demand_tag_targeting,
    validate_targeting,
)


class TestBuildDemandTagTargeting:
    """``build_demand_tag_targeting`` flattens AdCP overlay + product config
    directly onto demand-tag fields -- NOT into a nested ``targeting`` wrapper."""

    def test_empty_inputs_produce_empty_kwargs(self):
        assert build_demand_tag_targeting(None, None) == {}
        assert build_demand_tag_targeting(None, {}) == {}

    def test_supply_tag_ids_become_demand_tag_priorities(self):
        kwargs = build_demand_tag_targeting(None, {"supply_tag_ids": [1001, 1002]})
        assert kwargs["demand_tag_priorities"] == [
            {"supply_tag_id": 1001, "priority": 1, "tier": 1},
            {"supply_tag_id": 1002, "priority": 1, "tier": 1},
        ]

    def test_supply_tag_ids_coerce_str_to_int(self):
        """JSON product configs may carry IDs as strings; SS API needs ints."""
        kwargs = build_demand_tag_targeting(None, {"supply_tag_ids": ["1001", "1002"]})
        assert kwargs["demand_tag_priorities"][0]["supply_tag_id"] == 1001
        assert isinstance(kwargs["demand_tag_priorities"][0]["supply_tag_id"], int)

    def test_player_sizes_pass_through(self):
        kwargs = build_demand_tag_targeting(None, {"player_sizes": ["l", "xl"]})
        assert kwargs["player_sizes"] == ["l", "xl"]

    def test_device_types_pass_through(self):
        kwargs = build_demand_tag_targeting(None, {"device_types": ["ctv", "mobile"]})
        assert kwargs["user_agent_devices"] == ["ctv", "mobile"]

    def test_geo_country_overlay(self):
        overlay = MagicMock()
        overlay.geo_countries = [MagicMock(root="US"), MagicMock(root="CA")]
        overlay.geo_regions = None
        overlay.geo_metros = None
        overlay.device_type_any_of = None

        kwargs = build_demand_tag_targeting(overlay, None)
        assert kwargs["country_codes"] == ["US", "CA"]

    def test_geo_region_overlay(self):
        overlay = MagicMock()
        overlay.geo_countries = None
        overlay.geo_regions = [MagicMock(root="US-CA"), MagicMock(root="US-NY")]
        overlay.geo_metros = None
        overlay.device_type_any_of = None

        kwargs = build_demand_tag_targeting(overlay, None)
        assert kwargs["state_codes"] == ["US-CA", "US-NY"]

    def test_geo_metro_overlay_concatenates_values(self):
        overlay = MagicMock()
        overlay.geo_countries = None
        overlay.geo_regions = None
        overlay.geo_metros = [MagicMock(values=["501", "803"]), MagicMock(values=["807"])]
        overlay.device_type_any_of = None

        kwargs = build_demand_tag_targeting(overlay, None)
        assert kwargs["metro_area_codes"] == ["501", "803", "807"]

    def test_device_type_overlay_overrides_product_default(self):
        """AdCP overlay is more specific than product defaults -- it wins."""
        overlay = MagicMock()
        overlay.geo_countries = None
        overlay.geo_regions = None
        overlay.geo_metros = None
        overlay.device_type_any_of = ["ctv"]

        kwargs = build_demand_tag_targeting(overlay, {"device_types": ["mobile", "desktop"]})
        assert kwargs["user_agent_devices"] == ["ctv"]

    def test_extra_demand_tag_fields_escape_hatch_wins(self):
        """Raw escape-hatch fields override anything we built up."""
        kwargs = build_demand_tag_targeting(
            None,
            {
                "player_sizes": ["m"],
                "extra_demand_tag_fields": {"player_sizes": ["l", "xl"], "raw_field": True},
            },
        )
        assert kwargs["player_sizes"] == ["l", "xl"]
        assert kwargs["raw_field"] is True


class TestValidateTargeting:
    def test_none_overlay_is_valid(self):
        assert validate_targeting(None) == []

    def test_postal_targeting_rejected(self):
        overlay = MagicMock(spec=["geo_postal_areas", "geo_postal_areas_exclude"])
        overlay.geo_postal_areas = [MagicMock(values=["10001"])]
        overlay.geo_postal_areas_exclude = None
        errors = validate_targeting(overlay)
        assert any("postal" in e.lower() for e in errors)

    def test_frequency_cap_rejected(self):
        overlay = MagicMock(spec=["frequency_cap"])
        overlay.frequency_cap = {"impressions": 3, "period": "day"}
        errors = validate_targeting(overlay)
        assert any("frequency" in e.lower() for e in errors)

    def test_audience_targeting_rejected(self):
        overlay = MagicMock(spec=["audiences_any_of"])
        overlay.audiences_any_of = ["seg1"]
        errors = validate_targeting(overlay)
        assert any("audience" in e.lower() for e in errors)

    def test_dayparting_rejected(self):
        overlay = MagicMock(spec=["dayparting"])
        overlay.dayparting = [{"day": "mon"}]
        errors = validate_targeting(overlay)
        assert any("dayparting" in e.lower() for e in errors)


@pytest.mark.parametrize(
    "field,value",
    [
        ("geo_countries", []),
        ("geo_regions", []),
        ("geo_metros", []),
    ],
)
def test_empty_lists_in_overlay_are_no_op(field, value):
    overlay = MagicMock()
    overlay.geo_countries = []
    overlay.geo_regions = []
    overlay.geo_metros = []
    overlay.device_type_any_of = None
    setattr(overlay, field, value)
    kwargs = build_demand_tag_targeting(overlay, None)
    assert "country_codes" not in kwargs
    assert "state_codes" not in kwargs
    assert "metro_area_codes" not in kwargs


# ---------------------------------------------------------------------------
# Signal materialization: audience_include/exclude -> demand_tag_keys
# ---------------------------------------------------------------------------


def _ss_signal(signal_id: str, *, key_id: int, value_list_id: int, key_name: str = "station_id"):
    """Build a fake TenantSignal carrying the SpringServe passthrough shape
    that the /signals/bulk-create handler persists from the source-grid UI."""
    sig = MagicMock()
    sig.signal_id = signal_id
    sig.name = signal_id
    sig.adapter_config = {
        "type": "passthrough",
        "kind": "springserve_value_list",
        "key_id": str(key_id),
        "key_name": key_name,
        "value_list_id": str(value_list_id),
    }
    return sig


def _stub_uow_and_inventory(_monkeypatch, signals_by_id: dict, value_list_free_values: dict):
    """Stub TenantSignalUoW + SpringServeInventoryRepository so the
    materializer pulls signals + value_lists without a real DB.

    ``value_list_free_values`` maps ``value_list_id -> list[str]`` (the
    expanded free_values the publisher would have entered for that list).
    """
    from unittest.mock import patch

    sig_repo = MagicMock()
    sig_repo.list_by_ids.side_effect = lambda ids: [signals_by_id[i] for i in ids if i in signals_by_id]
    uow = MagicMock()
    uow.tenant_signals = sig_repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)

    # Fake inventory rows -- one ORM-like row per value_list.
    inv_rows = []
    for vid, free_values in value_list_free_values.items():
        row = MagicMock()
        row.entity_id = str(vid)
        row.raw_json = {"free_values": list(free_values)}
        inv_rows.append(row)
    inv_repo = MagicMock()
    inv_repo.list_by_type.return_value = inv_rows

    # The materializer opens a separate session via get_db_session() for
    # the inventory lookup. Stub that to a context manager that yields
    # nothing (the repo factory is what we control).
    fake_session_cm = MagicMock()
    fake_session_cm.__enter__ = MagicMock(return_value=MagicMock())
    fake_session_cm.__exit__ = MagicMock(return_value=False)

    patches = [
        patch("src.core.database.repositories.uow.TenantSignalUoW", return_value=uow),
        patch("src.adapters.springserve.targeting.get_db_session", create=True, return_value=fake_session_cm),
        patch(
            "src.core.database.repositories.springserve_inventory.SpringServeInventoryRepository",
            return_value=inv_repo,
        ),
    ]
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


class TestBuildDemandTagTargetingDropsSignals:
    """Signal targeting is NOT a body field on /demand_tags. The geo +
    device kwargs are still produced; demand_tag_keys / key_value_targeting
    must NOT appear in the kwargs dict (sub-resource POST handles them)."""

    def test_audience_include_does_not_leak_into_kwargs(self):
        overlay = MagicMock(
            spec=[
                "audience_include",
                "audience_exclude",
                "geo_countries",
                "geo_regions",
                "geo_metros",
                "device_type_any_of",
            ]
        )
        overlay.audience_include = ["sig_1"]
        overlay.audience_exclude = []
        overlay.geo_countries = None
        overlay.geo_regions = None
        overlay.geo_metros = None
        overlay.device_type_any_of = None
        kwargs = build_demand_tag_targeting(overlay, None, tenant_id="t1")
        assert "demand_tag_keys" not in kwargs
        assert "key_value_targeting" not in kwargs


class TestBuildDemandTagKvEntries:
    """``build_demand_tag_kv_entries`` resolves audience_include/exclude
    through tenant_signals + expands each value_list's free_values from
    the inventory cache. Output is the list of sub-resource POST payloads
    the adapter sends to ``/demand_tags/<id>/demand_tag_keys`` per the
    SpringServe docs (page 1628471383)."""

    def test_no_tenant_id_means_no_resolution(self):
        overlay = MagicMock(spec=["audience_include", "audience_exclude"])
        overlay.audience_include = ["sig_1"]
        overlay.audience_exclude = []
        assert build_demand_tag_kv_entries(overlay, tenant_id="") == []

    def test_single_include_expands_free_values_from_cache(self, monkeypatch):
        sig = _ss_signal("podcast_mv25", key_id=3997, value_list_id=2942, key_name="station_id")
        patches = _stub_uow_and_inventory(
            monkeypatch,
            signals_by_id={"podcast_mv25": sig},
            value_list_free_values={2942: ["1345713", "1334483"]},
        )
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["podcast_mv25"]
            overlay.audience_exclude = []

            entries = build_demand_tag_kv_entries(overlay, tenant_id="t1")

            assert entries == [
                {
                    "key_id": "3997",
                    "list_type": "white_list",
                    "group": "1",
                    "free_values": ["1345713", "1334483"],
                },
            ]
        finally:
            _stop(patches)

    def test_two_value_lists_same_key_merge_free_values(self, monkeypatch):
        """Multiple value_lists under one SpringServe key merge into one
        entry's ``free_values`` -- OR within entry."""
        s1 = _ss_signal("mv25", key_id=3997, value_list_id=2942)
        s2 = _ss_signal("mv35", key_id=3997, value_list_id=2945)
        patches = _stub_uow_and_inventory(
            monkeypatch,
            signals_by_id={"mv25": s1, "mv35": s2},
            value_list_free_values={
                2942: ["1345713"],
                2945: ["1286303", "122513"],
            },
        )
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["mv25", "mv35"]
            overlay.audience_exclude = []
            entries = build_demand_tag_kv_entries(overlay, tenant_id="t1")
            assert len(entries) == 1
            assert entries[0]["key_id"] == "3997"
            assert entries[0]["free_values"] == ["1345713", "1286303", "122513"]
        finally:
            _stop(patches)

    def test_two_keys_share_default_group_for_AND_semantics(self, monkeypatch):
        """All entries default to ``group="1"`` so SpringServe ANDs across
        keys (Sports AND CTV, not Sports OR CTV) -- matches the doc's
        same-group=AND semantics."""
        s1 = _ss_signal("audio_mv25", key_id=3997, value_list_id=2942)
        s2 = _ss_signal("ctv_app", key_id=3705, value_list_id=2600)
        patches = _stub_uow_and_inventory(
            monkeypatch,
            signals_by_id={"audio_mv25": s1, "ctv_app": s2},
            value_list_free_values={2942: ["1345713"], 2600: ["talpa_smarttv"]},
        )
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["audio_mv25", "ctv_app"]
            overlay.audience_exclude = []
            entries = build_demand_tag_kv_entries(overlay, tenant_id="t1")
            assert sorted(e["key_id"] for e in entries) == ["3705", "3997"]
            assert {e["group"] for e in entries} == {"1"}
        finally:
            _stop(patches)

    def test_exclude_emits_black_list(self, monkeypatch):
        sig = _ss_signal("not_kids", key_id=3997, value_list_id=2949)
        patches = _stub_uow_and_inventory(
            monkeypatch,
            signals_by_id={"not_kids": sig},
            value_list_free_values={2949: ["1286303"]},
        )
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = []
            overlay.audience_exclude = ["not_kids"]
            entries = build_demand_tag_kv_entries(overlay, tenant_id="t1")
            assert entries[0]["list_type"] == "black_list"
            assert entries[0]["free_values"] == ["1286303"]
        finally:
            _stop(patches)

    def test_missing_signal_raises(self, monkeypatch):
        patches = _stub_uow_and_inventory(monkeypatch, {}, {})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["nope"]
            overlay.audience_exclude = []
            with pytest.raises(ValueError, match="signal\\(s\\) not declared"):
                build_demand_tag_kv_entries(overlay, tenant_id="t1")
        finally:
            _stop(patches)

    def test_unsupported_kind_raises(self, monkeypatch):
        sig = MagicMock()
        sig.signal_id = "gam_segment"
        sig.adapter_config = {"type": "passthrough", "kind": "audience_segment", "segment_id": "12345"}
        patches = _stub_uow_and_inventory(monkeypatch, {"gam_segment": sig}, {})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["gam_segment"]
            overlay.audience_exclude = []
            with pytest.raises(ValueError, match="kind='audience_segment'.*not supported"):
                build_demand_tag_kv_entries(overlay, tenant_id="t1")
        finally:
            _stop(patches)

    def test_composed_signal_raises(self, monkeypatch):
        sig = MagicMock()
        sig.signal_id = "complex_sig"
        sig.adapter_config = {"type": "composed", "criteria": []}
        patches = _stub_uow_and_inventory(monkeypatch, {"complex_sig": sig}, {})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["complex_sig"]
            overlay.audience_exclude = []
            with pytest.raises(ValueError, match="composed.*not yet supported"):
                build_demand_tag_kv_entries(overlay, tenant_id="t1")
        finally:
            _stop(patches)

    def test_empty_audience_lists_short_circuit(self):
        overlay = MagicMock(spec=["audience_include", "audience_exclude"])
        overlay.audience_include = []
        overlay.audience_exclude = []
        assert build_demand_tag_kv_entries(overlay, tenant_id="t1") == []
