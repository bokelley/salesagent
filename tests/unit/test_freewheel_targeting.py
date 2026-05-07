"""Tests for FreeWheel targeting translation."""

from __future__ import annotations

from src.adapters.freewheel.targeting import build_targeting, validate_targeting
from src.core.schemas import Targeting


class TestBuildTargeting:
    def test_geo_country_becomes_geo_countries(self):
        targeting = Targeting(geo_countries=["US", "CA"])
        result = build_targeting(targeting, {})
        assert result["geo"]["countries"] == ["US", "CA"]

    def test_geo_metros_become_metros_list(self):
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
        )
        result = build_targeting(targeting, {})
        assert result["geo"]["metros"] == ["501", "803"]

    def test_targeting_profile_id_passes_through(self):
        result = build_targeting(None, {"targeting_profile_id": "tp_abc"})
        assert result["targetingProfileId"] == "tp_abc"

    def test_product_custom_targeting_emitted(self):
        result = build_targeting(None, {"custom_targeting": {"genre": ["sports"]}})
        assert result["customCriteria"] == [{"key": "genre", "values": ["sports"]}]

    def test_package_custom_overrides_product_custom(self):
        targeting = Targeting(custom={"freewheel": {"genre": ["news"]}})
        result = build_targeting(targeting, {"custom_targeting": {"genre": ["sports"]}})
        kv = {c["key"]: c["values"] for c in result["customCriteria"]}
        assert kv["genre"] == ["news"]

    def test_device_types_emitted(self):
        targeting = Targeting(geo_countries=["US"], device_type_any_of=["ctv", "mobile"])
        result = build_targeting(targeting, {})
        assert result["deviceTypes"] == ["ctv", "mobile"]

    def test_no_inputs_yields_empty_targeting(self):
        assert build_targeting(None, None) == {}


class TestValidateTargeting:
    def test_postal_areas_rejected(self):
        targeting = Targeting(
            geo_countries=["US"],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
        )
        errors = validate_targeting(targeting)
        assert any("postal" in e.lower() for e in errors)

    def test_standard_geo_passes(self):
        targeting = Targeting(geo_countries=["US"], geo_regions=["US-NY"])
        assert validate_targeting(targeting) == []

    def test_none_targeting_yields_no_errors(self):
        assert validate_targeting(None) == []
