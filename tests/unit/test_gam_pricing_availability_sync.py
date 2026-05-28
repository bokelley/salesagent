"""Unit tests for product-level GAM pricing/availability persistence helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adcp.types import DeliveryForecast

from src.core.database.models import PricingOption, Product
from src.services.gam_pricing_availability_sync import (
    _apply_pricing_guidance,
    _product_guidance_from_line_items,
    _product_targeted_placement_ids,
    _report_country_filters,
    _rows_for_product,
)


def _product(*, countries: list[str] | None = None) -> Product:
    product = Product(
        tenant_id="tenant_1",
        product_id="sports_display",
        name="Sports Display",
        description="Sports display inventory",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        targeting_template={},
        delivery_type="non_guaranteed",
        property_tags=["all_inventory"],
        implementation_config={"targeted_placement_ids": ["123", "456"]},
        countries=countries,
        delivery_measurement={"provider": "google_ad_manager"},
    )
    product.pricing_options = [
        PricingOption(
            tenant_id="tenant_1",
            product_id="sports_display",
            pricing_model="cpm",
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 0.5},
            min_spend_per_package=Decimal("20.00"),
        ),
        PricingOption(
            tenant_id="tenant_1",
            product_id="sports_display",
            pricing_model="cpc",
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 0.05},
        ),
    ]
    return product


def _line_item(
    *,
    placement_id: str,
    country_code: str = "US",
    country: str = "United States",
    impressions: int,
    cpm: float,
    clicks: int = 0,
    viewable_impressions: int = 0,
    measurable_impressions: int = 0,
    completed_views: int = 0,
) -> dict:
    revenue = round(impressions / 1000 * cpm, 2)
    return {
        "placement_id": placement_id,
        "placement_name": f"Placement {placement_id}",
        "country_code": country_code,
        "country": country,
        "line_item_id": f"li_{placement_id}_{impressions}",
        "line_item_name": "Line item",
        "line_item_type": "PRICE_PRIORITY",
        "impressions": impressions,
        "viewable_impressions": viewable_impressions,
        "measurable_impressions": measurable_impressions,
        "clicks": clicks,
        "completed_views": completed_views,
        "revenue": revenue,
        "cpm": cpm,
        "vcpm": round(revenue / viewable_impressions * 1000, 4) if viewable_impressions else None,
        "cpc": round(revenue / clicks, 4) if clicks else None,
        "cpcv": round(revenue / completed_views, 4) if completed_views else None,
    }


def test_product_targeted_placement_ids_reads_effective_config() -> None:
    assert _product_targeted_placement_ids(_product()) == ["123", "456"]


def test_rows_for_product_filters_by_placement_and_country() -> None:
    rows = [
        _line_item(placement_id="123", country_code="US", impressions=10_000, cpm=1.0),
        _line_item(placement_id="123", country_code="CA", country="Canada", impressions=10_000, cpm=1.0),
        _line_item(placement_id="999", country_code="US", impressions=10_000, cpm=1.0),
    ]

    filtered = _rows_for_product(rows, {"placement_ids": ["123"], "countries": {"us"}})

    assert [row["placement_id"] for row in filtered] == ["123"]
    assert [row["country_code"] for row in filtered] == ["US"]


def test_report_country_filters_only_when_every_product_has_country_targeting() -> None:
    assert _report_country_filters(
        [
            {"report_countries": ["US"]},
            {"report_countries": ["Canada"]},
        ]
    ) == ["Canada", "US"]
    assert _report_country_filters([{"report_countries": ["US"]}, {"report_countries": []}]) is None


def test_product_guidance_builds_forecast_bookability_and_pricing_guidance() -> None:
    product = _product()
    rows = [
        _line_item(
            placement_id="123",
            impressions=10_000,
            cpm=1.0,
            clicks=20,
            viewable_impressions=6_000,
            measurable_impressions=8_000,
            completed_views=500,
        ),
        _line_item(
            placement_id="456",
            impressions=30_000,
            cpm=2.0,
            clicks=90,
            viewable_impressions=20_000,
            measurable_impressions=25_000,
            completed_views=2_000,
        ),
    ]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={"minimum_package_budget": 20.0},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={
            "date_range": "this_month",
            "window_start": "2026-05-01",
            "window_end": "2026-05-28",
            "filters": {"line_item_types": ["PRICE_PRIORITY"]},
        },
        currency_limits={},
    )

    forecast = DeliveryForecast.model_validate(guidance["forecast"])
    assert forecast.points[0].product_id == "sports_display"
    assert forecast.points[0].metrics.impressions.mid == 40_000
    assert forecast.points[0].metrics.clicks.mid == 110
    assert forecast.points[0].viewability is not None
    assert forecast.points[0].viewability.viewable_impressions.mid == 26_000
    assert forecast.points[0].viewability.viewable_rate.mid == 0.787879
    assert guidance["pricing_guidance_by_model"]["cpm"] == {"p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}
    assert guidance["pricing_guidance_by_model"]["cpc"]["p25"] == 0.67
    assert guidance["bookability"]["bookable"] is True
    assert guidance["bookability"]["options"][0]["required_units"] == 20_000

    assert _apply_pricing_guidance(product.pricing_options, guidance) == 2
    assert product.pricing_options[0].price_guidance == {"floor": 0.5, "p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}


def test_apply_pricing_guidance_adds_floor_fallback_for_legacy_auction_options() -> None:
    option = PricingOption(
        tenant_id="tenant_1",
        product_id="sports_display",
        pricing_model="cpm",
        rate=None,
        currency="USD",
        is_fixed=False,
        price_guidance={},
    )

    assert (
        _apply_pricing_guidance(
            [option],
            {"pricing_guidance_by_model": {"cpm": {"p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}}},
        )
        == 1
    )
    assert option.price_guidance == {"floor": 1.0, "p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}


def test_product_guidance_marks_product_unbookable_when_conservative_capacity_is_too_small() -> None:
    product = _product()
    rows = [_line_item(placement_id="123", impressions=10_000, cpm=1.0)]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={"date_range": "this_month", "filters": {}},
        currency_limits={},
    )

    assert guidance["bookability"]["bookable"] is False
    assert guidance["bookability"]["options"][0]["reason"] == "insufficient_capacity"


def test_product_guidance_uses_capacity_minimum_for_bookability() -> None:
    product = _product()
    rows = [_line_item(placement_id="123", impressions=100_000, cpm=1.0)]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={"minimum_package_budget": 620.0},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={"date_range": "this_month", "filters": {}},
        currency_limits={},
    )

    cpm_option = guidance["bookability"]["options"][0]
    assert cpm_option["minimum_package_budget"] == 620.0
    assert cpm_option["required_units"] == 620_000
    assert cpm_option["reason"] == "insufficient_capacity"


def test_product_guidance_marks_mismatched_currency_unbookable_and_skips_guidance() -> None:
    product = _product()
    product.pricing_options[0].currency = "EUR"
    rows = [_line_item(placement_id="123", impressions=100_000, cpm=1.0)]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={"date_range": "this_month", "filters": {}},
        currency_limits={},
    )

    assert guidance["bookability"]["options"][0]["reason"] == "currency_conversion_unavailable"
    assert _apply_pricing_guidance(product.pricing_options, guidance) == 0
    assert product.pricing_options[0].price_guidance == {"floor": 0.5}
