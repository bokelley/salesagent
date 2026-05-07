"""Tests for Triton TAP targeting translation."""

from __future__ import annotations

from src.adapters.triton.targeting import build_targeting_rules, validate_targeting
from src.core.schemas import Targeting


class TestBuildTargetingRules:
    def test_product_station_ids_become_in_rule(self):
        rules = build_targeting_rules(None, {"station_ids": ["KROQ", "KIIS"]})
        assert {"type": "in", "dimension": "station", "values": ["KROQ", "KIIS"]} in rules

    def test_package_custom_station_overrides_product(self):
        targeting = Targeting(custom={"triton": {"station_ids": ["WXYZ"]}})
        rules = build_targeting_rules(targeting, {"station_ids": ["KROQ"]})
        station_rule = next(r for r in rules if r["dimension"] == "station")
        assert station_rule["values"] == ["WXYZ"]

    def test_geo_country_becomes_country_rule(self):
        targeting = Targeting(geo_countries=["US", "CA"])
        rules = build_targeting_rules(targeting, {})
        assert {"type": "in", "dimension": "country", "values": ["US", "CA"]} in rules

    def test_geo_metros_become_market_rule(self):
        targeting = Targeting(
            geo_countries=["US"],
            geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
        )
        rules = build_targeting_rules(targeting, {})
        market_rule = next((r for r in rules if r["dimension"] == "market"), None)
        assert market_rule is not None
        assert market_rule["values"] == ["501", "803"]

    def test_genres_become_station_genre_shoutcast(self):
        rules = build_targeting_rules(None, {"genres": ["Rock", "Pop"]})
        assert {"type": "in", "dimension": "station-genre-shoutcast", "values": ["Rock", "Pop"]} in rules

    def test_no_inputs_yields_no_rules(self):
        assert build_targeting_rules(None, None) == []
        assert build_targeting_rules(None, {}) == []

    def test_combines_station_geo_and_daypart(self):
        targeting = Targeting(geo_countries=["US"])
        rules = build_targeting_rules(targeting, {"station_ids": ["KROQ"], "daypart_ids": ["dp_morning"]})
        dimensions = {r["dimension"] for r in rules}
        assert dimensions == {"station", "country", "daypart"}


class TestValidateTargeting:
    def test_audio_device_passes(self):
        targeting = Targeting(geo_countries=["US"], device_type_any_of=["mobile", "desktop", "audio"])
        assert validate_targeting(targeting) == []

    def test_ctv_device_rejected(self):
        targeting = Targeting(geo_countries=["US"], device_type_any_of=["ctv"])
        errors = validate_targeting(targeting)
        assert any("ctv" in e.lower() for e in errors)

    def test_video_media_type_rejected(self):
        targeting = Targeting(geo_countries=["US"], media_type_any_of=["audio", "olv"])
        errors = validate_targeting(targeting)
        assert any("olv" in e for e in errors)

    def test_iab_categories_rejected_with_genre_hint(self):
        targeting = Targeting(geo_countries=["US"], content_cat_any_of=["IAB1"])
        errors = validate_targeting(targeting)
        assert any("genre" in e.lower() for e in errors)

    def test_none_targeting_yields_no_errors(self):
        assert validate_targeting(None) == []
