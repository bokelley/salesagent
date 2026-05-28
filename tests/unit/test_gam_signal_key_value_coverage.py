"""Unit tests for GAM custom-targeting signal coverage guidance."""

from __future__ import annotations

from typing import Any

from adcp.types.generated_poc.core.signal_coverage_forecast import SignalCoverageForecast

from src.adapters.gam_reporting_service import GAMReportingService


def _service_for_signal_coverage(report_results: list[list[dict[str, str]]], captured: list[dict[str, Any]]):
    service = GAMReportingService.__new__(GAMReportingService)
    service.network_timezone = "America/New_York"

    def fake_run_report(report_query: dict[str, Any]) -> list[dict[str, str]]:
        captured.append(report_query)
        return report_results.pop(0)

    service._run_report = fake_run_report  # type: ignore[method-assign]
    service._custom_targeting_key = lambda **_: {  # type: ignore[method-assign]
        "id": "42",
        "name": "weather",
        "display_name": "Weather",
        "type": "PREDEFINED",
        "reportable_type": "ON",
        "status": "ACTIVE",
    }
    service._custom_targeting_values = lambda **_: [  # type: ignore[method-assign]
        {"id": "100", "name": "hot", "display_name": "Hot"},
        {"id": "200", "name": "cold", "display_name": "Cold"},
        {"id": "300", "name": "rainy", "display_name": "Rainy"},
    ]
    return service


def _value_row(value: str, value_id: str, impressions: int, cpm: float) -> dict[str, str]:
    revenue_micros = int((impressions / 1000) * cpm * 1_000_000)
    return {
        "Dimension.CUSTOM_CRITERIA": f"weather={value}",
        "Dimension.LINE_ITEM_TYPE": "PRICE_PRIORITY",
        "Dimension.CUSTOM_TARGETING_VALUE_ID": value_id,
        "Column.AD_SERVER_IMPRESSIONS": str(impressions),
        "Column.AD_SERVER_CPM_AND_CPC_REVENUE": str(revenue_micros),
    }


def test_custom_targeting_value_coverage_returns_distribution_with_not_present_bucket():
    captured: list[dict[str, Any]] = []
    service = _service_for_signal_coverage(
        [
            [
                {
                    "Dimension.LINE_ITEM_TYPE": "PRICE_PRIORITY",
                    "Column.AD_SERVER_IMPRESSIONS": "1000",
                    "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "2000000",
                }
            ],
            [
                _value_row("hot", "100", 180, 1.00),
                _value_row("cold", "200", 380, 2.00),
                _value_row("rainy", "300", 160, 3.00),
            ],
        ],
        captured,
    )

    result = service.get_custom_targeting_value_coverage(
        "this_month",
        key_name="weather",
        requested_timezone="America/New_York",
    )

    assert result["key"]["name"] == "weather"
    assert result["total_inventory"] == {"impressions": 1000, "revenue": 2.0, "average_cpm": 2.0}
    assert result["not_present"] == {
        "label": "not present",
        "impressions": 280,
        "share_of_inventory": 0.28,
    }
    assert result["coverage"] == {
        "present_impressions": 720,
        "present_share_of_inventory": 0.72,
        "value_count": 3,
        "registered_value_count": 3,
        "multi_value_overlap": False,
    }
    assert [(row["value"], row["impressions"], row["share_of_inventory"]) for row in result["values"]] == [
        ("cold", 380, 0.38),
        ("hot", 180, 0.18),
        ("rainy", 160, 0.16),
    ]
    forecast = SignalCoverageForecast.model_validate(result["coverage_forecast"])
    assert forecast.forecast_range_unit == "availability"
    assert forecast.scope.kind.value == "inventory"
    assert forecast.scope.line_item_types == ["PRICE_PRIORITY"]
    assert forecast.bucket_semantics.value == "exclusive"
    assert forecast.bucket_completeness.value == "partial"
    assert [
        (point.label, point.metrics.impressions.mid, point.metrics.coverage_rate.mid) for point in forecast.points
    ] == [
        ("Cold", 380, 0.38),
        ("Hot", 180, 0.18),
        ("Rainy", 160, 0.16),
        ("not present", 280, 0.28),
    ]

    assert captured[0]["reportQuery"]["statement"]["query"] == "WHERE LINE_ITEM_TYPE IN ('PRICE_PRIORITY')"
    assert captured[1]["reportQuery"]["statement"]["query"] == (
        "WHERE LINE_ITEM_TYPE IN ('PRICE_PRIORITY') AND CUSTOM_TARGETING_VALUE_ID IN (100, 200, 300)"
    )
    assert captured[1]["reportQuery"]["dimensions"] == [
        "CUSTOM_TARGETING_VALUE_ID",
        "CUSTOM_CRITERIA",
        "LINE_ITEM_TYPE",
    ]


def test_bulk_custom_targeting_value_coverage_uses_one_baseline_and_value_chunks():
    captured: list[dict[str, Any]] = []
    service = _service_for_signal_coverage(
        [
            [
                {
                    "Dimension.LINE_ITEM_TYPE": "PRICE_PRIORITY",
                    "Column.AD_SERVER_IMPRESSIONS": "1000",
                    "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "2000000",
                }
            ],
            [
                _value_row("hot", "100", 180, 1.00),
                _value_row("cold", "200", 380, 2.00),
            ],
            [_value_row("rainy", "300", 160, 3.00)],
        ],
        captured,
    )

    result = service.get_custom_targeting_value_coverage_for_value_ids(
        "this_month",
        value_ids=[str(value_id) for value_id in range(100, 301)],
        values_by_id={
            "100": {"id": "100", "name": "hot", "display_name": "Hot"},
            "200": {"id": "200", "name": "cold", "display_name": "Cold"},
            "300": {"id": "300", "name": "rainy", "display_name": "Rainy"},
        },
        requested_timezone="America/New_York",
    )

    assert result["total_inventory"]["impressions"] == 1000
    assert result["coverage"] == {
        "present_impressions": 720,
        "value_count": 3,
        "requested_value_count": 201,
        "report_value_chunk_count": 2,
    }
    assert [(row["value_id"], row["value"], row["share_of_inventory"]) for row in result["values"]] == [
        ("200", "cold", 0.38),
        ("100", "hot", 0.18),
        ("300", "rainy", 0.16),
    ]
    assert len(captured) == 3
    assert captured[0]["reportQuery"]["statement"]["query"] == "WHERE LINE_ITEM_TYPE IN ('PRICE_PRIORITY')"
    assert captured[1]["reportQuery"]["statement"]["query"].endswith(
        "CUSTOM_TARGETING_VALUE_ID IN (" + ", ".join(str(i) for i in range(100, 300)) + ")"
    )
    assert captured[2]["reportQuery"]["statement"]["query"].endswith("CUSTOM_TARGETING_VALUE_ID IN (300)")


def test_custom_targeting_value_coverage_flags_multivalue_overlap():
    captured: list[dict[str, Any]] = []
    service = _service_for_signal_coverage(
        [
            [
                {
                    "Dimension.LINE_ITEM_TYPE": "PRICE_PRIORITY",
                    "Column.AD_SERVER_IMPRESSIONS": "1000",
                    "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "1000000",
                }
            ],
            [
                _value_row("hot", "100", 700, 1.00),
                _value_row("cold", "200", 500, 1.00),
            ],
        ],
        captured,
    )

    result = service.get_custom_targeting_value_coverage(
        "this_month",
        key_name="weather",
        min_value_impressions=1,
    )

    assert result["coverage"]["multi_value_overlap"] is True
    assert result["coverage"]["present_impressions"] == 1200
    assert result["coverage"]["present_share_of_inventory"] == 1.0
    assert result["not_present"]["impressions"] == 0
    assert result["coverage_forecast"]["bucket_semantics"] == "overlapping"
