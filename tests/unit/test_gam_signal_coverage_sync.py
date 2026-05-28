"""Unit tests for persisting GAM signal coverage forecasts."""

from __future__ import annotations

from typing import Any

from adcp.types.generated_poc.core.signal_coverage_forecast import SignalCoverageForecast

from src.core.database.models import TenantSignal
from src.services.gam_signal_coverage_sync import (
    _coverage_forecast_for_signal,
    _custom_key_value_signals_by_key,
)


def _tenant_signal(signal_id: str, *, key_id: str = "42", value_id: str = "100") -> TenantSignal:
    return TenantSignal(
        tenant_id="tenant_1",
        signal_id=signal_id,
        name=f"Weather={signal_id}",
        value_type="binary",
        categories=[],
        tags=[],
        adapter_config={
            "type": "passthrough",
            "kind": "custom_key_value",
            "key_id": key_id,
            "value_id": value_id,
        },
    )


def _key_coverage(values: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "date_range": "this_month",
        "window_start": "2026-05-01",
        "window_end": "2026-05-28",
        "timezone": "America/New_York",
        "key": {"id": "42", "name": "weather", "display_name": "Weather"},
        "filters": {"line_item_types": ["PRICE_PRIORITY"]},
        "total_inventory": {"impressions": 1000, "revenue": 2.0, "average_cpm": 2.0},
        "values": values
        or [
            {
                "value_id": "100",
                "value": "hot",
                "display_name": "Hot",
                "impressions": 180,
                "revenue": 0.18,
                "share_of_inventory": 0.18,
            }
        ],
    }


def test_coverage_forecast_for_signal_builds_binary_present_absent_shape() -> None:
    forecast_dict = _coverage_forecast_for_signal(_tenant_signal("weather_hot"), _key_coverage())
    assert forecast_dict is not None

    forecast = SignalCoverageForecast.model_validate(forecast_dict)

    assert forecast.forecast_range_unit == "availability"
    assert forecast.scope.kind.value == "inventory"
    assert forecast.scope.custom_targeting_key_id == "42"
    assert forecast.scope.custom_targeting_value_id == "100"
    assert forecast.bucket_semantics.value == "exclusive"
    assert [
        (point.label, point.metrics.impressions.mid, point.metrics.coverage_rate.mid) for point in forecast.points
    ] == [
        ("Hot", 180, 0.18),
        ("not present", 820, 0.82),
    ]
    dumped = forecast.model_dump(mode="json", exclude_none=True)
    assert dumped["points"][0]["dimensions"][0]["signal_id"] == "weather_hot"
    assert dumped["points"][0]["dimensions"][0]["presence"] == "present"
    assert "generated_at" in dumped
    assert "valid_until" in dumped


def test_coverage_forecast_sanitizes_signal_id_for_wire_shape() -> None:
    forecast_dict = _coverage_forecast_for_signal(_tenant_signal("audience.sports_fans"), _key_coverage())
    assert forecast_dict is not None

    dumped = SignalCoverageForecast.model_validate(forecast_dict).model_dump(mode="json", exclude_none=True)

    assert dumped["points"][0]["dimensions"][0]["signal_id"] == "audience_sports_fans"


def test_coverage_forecast_for_signal_handles_registered_value_with_no_delivery() -> None:
    forecast_dict = _coverage_forecast_for_signal(
        _tenant_signal("weather_cold", value_id="200"),
        _key_coverage(values=[]),
    )
    assert forecast_dict is not None

    forecast = SignalCoverageForecast.model_validate(forecast_dict)

    assert [
        (point.label, point.metrics.impressions.mid, point.metrics.coverage_rate.mid) for point in forecast.points
    ] == [
        ("200", 0, 0.0),
        ("not present", 1000, 1.0),
    ]


def test_custom_key_value_signals_group_by_key_and_skip_other_shapes() -> None:
    key_value = _tenant_signal("weather_hot", key_id="42", value_id="100")
    audience = TenantSignal(
        tenant_id="tenant_1",
        signal_id="sports_fans",
        name="Sports Fans",
        value_type="binary",
        categories=[],
        tags=[],
        adapter_config={"type": "passthrough", "kind": "audience_segment", "segment_id": "900"},
    )
    incomplete = _tenant_signal("bad", key_id="42", value_id="")

    grouped = _custom_key_value_signals_by_key([key_value, audience, incomplete])

    assert grouped == {"42": [key_value]}
