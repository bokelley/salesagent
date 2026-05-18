"""Tests for SpringServe targeting translation -- AdCP overlay -> demand-tag fields."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.springserve.targeting import build_demand_tag_targeting, validate_targeting


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


def _stub_uow(_monkeypatch, signals_by_id: dict):
    """Stub TenantSignalUoW so the materializer pulls signals out of a fake
    repo without needing a real DB session."""
    from unittest.mock import patch

    repo = MagicMock()
    repo.list_by_ids.side_effect = lambda ids: [signals_by_id[i] for i in ids if i in signals_by_id]
    uow = MagicMock()
    uow.tenant_signals = repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    p = patch("src.core.database.repositories.uow.TenantSignalUoW", return_value=uow)
    p.start()
    return p, repo


class TestSignalMaterialization:
    """``build_demand_tag_targeting`` resolves audience_include/exclude
    through tenant_signals -> emits ``demand_tag_keys`` + flips
    ``key_value_targeting`` per the SpringServe demand-tag wire format
    verified live (Talpa demand_tags 1466880, 1703109)."""

    def test_no_tenant_id_means_no_signal_resolution(self, monkeypatch):
        """Callers that omit tenant_id can't resolve signals -- the bulk-map
        UI handles that path; ad-hoc dry runs should be no-ops."""
        overlay = MagicMock(spec=["audience_include", "audience_exclude"])
        overlay.audience_include = ["sig_1"]
        overlay.audience_exclude = []
        kwargs = build_demand_tag_targeting(overlay, None, tenant_id=None)
        assert "demand_tag_keys" not in kwargs
        assert "key_value_targeting" not in kwargs

    def test_single_include_signal_emits_white_list_entry(self, monkeypatch):
        sig = _ss_signal("podcast_mv25", key_id=3997, value_list_id=2942, key_name="station_id")
        p, _ = _stub_uow(monkeypatch, {"podcast_mv25": sig})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["podcast_mv25"]
            overlay.audience_exclude = []

            kwargs = build_demand_tag_targeting(overlay, None, tenant_id="t1")

            assert kwargs["key_value_targeting"] is True
            assert kwargs["demand_tag_keys"] == [
                {
                    "key_id": 3997,
                    "list_type": "white_list",
                    "demand_tag_key_type": "values",
                    "key_required": True,
                    "group": "1",
                    "free_values": [],
                    "value_ids": [],
                    "value_list_ids": [2942],
                },
            ]
        finally:
            p.stop()

    def test_two_includes_same_key_share_one_entry_two_value_lists(self, monkeypatch):
        """Multiple value_lists under the same SpringServe key collapse into
        one demand_tag_keys entry -- SpringServe ORs value_lists within a key."""
        s1 = _ss_signal("mv25", key_id=3997, value_list_id=2942)
        s2 = _ss_signal("mv35_54", key_id=3997, value_list_id=2945)
        p, _ = _stub_uow(monkeypatch, {"mv25": s1, "mv35_54": s2})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["mv25", "mv35_54"]
            overlay.audience_exclude = []

            kwargs = build_demand_tag_targeting(overlay, None, tenant_id="t1")

            assert len(kwargs["demand_tag_keys"]) == 1
            entry = kwargs["demand_tag_keys"][0]
            assert entry["key_id"] == 3997
            assert entry["list_type"] == "white_list"
            assert entry["value_list_ids"] == [2942, 2945]
        finally:
            p.stop()

    def test_includes_on_distinct_keys_get_distinct_groups(self, monkeypatch):
        """Two keys = two demand_tag_keys entries with different ``group``
        indices so SpringServe ANDs across them."""
        s1 = _ss_signal("audio_mv25", key_id=3997, value_list_id=2942, key_name="station_id")
        s2 = _ss_signal("ctv_app", key_id=3705, value_list_id=2600, key_name="fwprof")
        p, _ = _stub_uow(monkeypatch, {"audio_mv25": s1, "ctv_app": s2})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["audio_mv25", "ctv_app"]
            overlay.audience_exclude = []

            kwargs = build_demand_tag_targeting(overlay, None, tenant_id="t1")

            entries = sorted(kwargs["demand_tag_keys"], key=lambda e: e["key_id"])
            assert [e["key_id"] for e in entries] == [3705, 3997]
            assert {e["group"] for e in entries} == {"1", "2"}
        finally:
            p.stop()

    def test_exclude_emits_black_list(self, monkeypatch):
        sig = _ss_signal("orphan_demo", key_id=3997, value_list_id=2949)
        p, _ = _stub_uow(monkeypatch, {"orphan_demo": sig})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = []
            overlay.audience_exclude = ["orphan_demo"]

            kwargs = build_demand_tag_targeting(overlay, None, tenant_id="t1")

            assert kwargs["demand_tag_keys"][0]["list_type"] == "black_list"
            assert kwargs["demand_tag_keys"][0]["value_list_ids"] == [2949]
        finally:
            p.stop()

    def test_missing_signal_raises_with_descriptive_message(self, monkeypatch):
        p, _ = _stub_uow(monkeypatch, {})  # empty repo
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["nonexistent_sig"]
            overlay.audience_exclude = []

            with pytest.raises(ValueError, match="signal\\(s\\) not declared"):
                build_demand_tag_targeting(overlay, None, tenant_id="t1")
        finally:
            p.stop()

    def test_unsupported_kind_raises(self, monkeypatch):
        """GAM-shaped signals don't materialize against SpringServe."""
        sig = MagicMock()
        sig.signal_id = "gam_segment"
        sig.adapter_config = {
            "type": "passthrough",
            "kind": "audience_segment",  # GAM kind
            "segment_id": "12345",
        }
        p, _ = _stub_uow(monkeypatch, {"gam_segment": sig})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["gam_segment"]
            overlay.audience_exclude = []

            with pytest.raises(ValueError, match="kind='audience_segment'.*not supported"):
                build_demand_tag_targeting(overlay, None, tenant_id="t1")
        finally:
            p.stop()

    def test_composed_signal_raises(self, monkeypatch):
        sig = MagicMock()
        sig.signal_id = "complex_sig"
        sig.adapter_config = {"type": "composed", "criteria": [{"kind": "springserve_value_list"}]}
        p, _ = _stub_uow(monkeypatch, {"complex_sig": sig})
        try:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = ["complex_sig"]
            overlay.audience_exclude = []

            with pytest.raises(ValueError, match="composed.*not yet supported"):
                build_demand_tag_targeting(overlay, None, tenant_id="t1")
        finally:
            p.stop()

    def test_empty_audience_lists_skip_signal_resolution(self, monkeypatch):
        """If both audience_include and audience_exclude are empty, the UoW
        is never opened -- saves a round-trip when buyers don't supply signals."""
        from unittest.mock import patch

        with patch("src.core.database.repositories.uow.TenantSignalUoW") as MockUoW:
            overlay = MagicMock(spec=["audience_include", "audience_exclude"])
            overlay.audience_include = []
            overlay.audience_exclude = []
            kwargs = build_demand_tag_targeting(overlay, None, tenant_id="t1")
            assert "demand_tag_keys" not in kwargs
            MockUoW.assert_not_called()
