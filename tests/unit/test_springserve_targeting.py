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
