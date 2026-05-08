"""Tests for GAMForecastManager.

Covers tescoboy issue #152: pre-flight overbook detection requires the
GAM availability forecast call. The manager itself is the GAM SOAP
wrapper; the detector that uses it lives in
``test_create_media_buy_overbook.py``.

These tests target the manager's two public methods:
- ``get_for_product`` (the products-page persistence path; ports
  tescoboy's branch)
- ``get_available_units`` (the convenience wrapper added here for the
  pre-flight gate; takes explicit ad_unit_ids + dates instead of a
  Product)

The manager NEVER raises — every error path returns a structured
``ForecastResult.error`` (or ``None`` from ``get_available_units``).
This is the contract the overbook gate relies on for fail-open behavior.
"""

from datetime import date
from unittest.mock import MagicMock

from src.adapters.gam.managers.forecast import (
    ForecastResult,
    GAMForecastManager,
    _build_spec_compliant_forecast,
    _to_gam_datetime,
)


def _build_manager(forecast_response, advertiser_id="adv_1"):
    """Construct a manager with a mocked ForecastService."""
    forecast_service = MagicMock()
    forecast_service.getAvailabilityForecast.return_value = forecast_response

    client_manager = MagicMock()
    client_manager.get_service.return_value = forecast_service

    return GAMForecastManager(client_manager=client_manager, advertiser_id=advertiser_id), forecast_service


class TestToGamDatetimeHelper:
    def test_emits_noon_to_avoid_past_rejection(self):
        result = _to_gam_datetime(date(2026, 5, 7))
        # GAM rejects START_DATE_TIME_IS_IN_PAST when "today" is passed
        # at midnight; noon publisher time is the safe anchor.
        assert result["hour"] == 12
        assert result["minute"] == 0
        assert result["timeZoneId"] == "America/New_York"

    def test_explicit_timezone_threaded_through(self):
        result = _to_gam_datetime(date(2026, 5, 7), time_zone_id="America/Los_Angeles")
        assert result["timeZoneId"] == "America/Los_Angeles"


class TestBuildSpecCompliantForecast:
    def test_method_is_estimate_not_guaranteed(self):
        # GAM availability forecasts are an estimate, not a guarantee —
        # AdCP DeliveryForecast.method must reflect that.
        forecast = _build_spec_compliant_forecast(50_000, label="t", currency="USD")
        assert forecast["method"] == "estimate"
        assert forecast["currency"] == "USD"

    def test_single_point_carries_mid_only(self):
        # GAM doesn't return low/high bounds; spec marks them optional.
        forecast = _build_spec_compliant_forecast(50_000, label="t", currency="USD")
        assert len(forecast["points"]) == 1
        metrics = forecast["points"][0]["metrics"]
        assert metrics == {"impressions": {"mid": 50000.0}}


class TestGetAvailableUnitsHappyPath:
    def test_single_ad_unit_returns_units(self):
        response = MagicMock()
        response.availableUnits = 25_000
        manager, _ = _build_manager(response)

        result = manager.get_available_units(
            ad_unit_ids=["ad_unit_1"],
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 14),
        )
        assert result == 25_000

    def test_multiple_ad_units_threaded_into_targeting(self):
        response = MagicMock()
        response.availableUnits = 100_000
        manager, forecast_service = _build_manager(response)

        manager.get_available_units(
            ad_unit_ids=["ad_unit_1", "ad_unit_2", "ad_unit_3"],
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 14),
        )

        prospective = forecast_service.getAvailabilityForecast.call_args.args[0]
        targeted = prospective["lineItem"]["targeting"]["inventoryTargeting"]["targetedAdUnits"]
        assert {t["adUnitId"] for t in targeted} == {"ad_unit_1", "ad_unit_2", "ad_unit_3"}

    def test_advertiser_id_attached_at_top_level_not_inside_line_item(self):
        # Verified via zeep introspection on the v202602 WSDL — putting
        # advertiserId inside lineItem triggers KeyError from the SOAP
        # serializer. Pin the structural contract.
        response = MagicMock()
        response.availableUnits = 1
        manager, forecast_service = _build_manager(response, advertiser_id="adv_xyz")

        manager.get_available_units(
            ad_unit_ids=["ad_unit_1"],
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 14),
        )

        prospective = forecast_service.getAvailabilityForecast.call_args.args[0]
        assert prospective["advertiserId"] == "adv_xyz"
        assert "advertiserId" not in prospective["lineItem"]


class TestGetAvailableUnitsFailOpen:
    def test_empty_ad_units_returns_none_without_calling_gam(self):
        manager, forecast_service = _build_manager(MagicMock())
        result = manager.get_available_units(
            ad_unit_ids=[],
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 14),
        )
        assert result is None
        forecast_service.getAvailabilityForecast.assert_not_called()

    def test_null_available_units_returns_none(self):
        # GAM returns null when targeting is misconfigured or the window
        # is outside the forecast horizon. The gate fails open.
        response = MagicMock()
        response.availableUnits = None
        manager, _ = _build_manager(response)

        result = manager.get_available_units(
            ad_unit_ids=["ad_unit_1"],
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 14),
        )
        assert result is None

    def test_soap_exception_returns_none(self):
        client_manager = MagicMock()
        forecast_service = MagicMock()
        forecast_service.getAvailabilityForecast.side_effect = RuntimeError("ForecastingError.NO_FORECAST_YET")
        client_manager.get_service.return_value = forecast_service
        manager = GAMForecastManager(client_manager=client_manager, advertiser_id="adv_1")

        result = manager.get_available_units(
            ad_unit_ids=["ad_unit_1"],
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 14),
        )
        assert result is None


class TestGetForProductSpecCompliance:
    """`get_for_product` must NEVER return a half-shaped DeliveryForecast.

    The contract documented at the top of forecast.py: on any error,
    ``forecast=None`` and the diagnostic lands in ``error``. Spec
    validation is non-negotiable for the field that gets persisted to
    ``products.forecast``.
    """

    def test_missing_targeting_returns_error_no_forecast(self):
        manager, _ = _build_manager(MagicMock())
        product = MagicMock(implementation_config={"line_item_type": "STANDARD"})

        result = manager.get_for_product(product)

        assert isinstance(result, ForecastResult)
        assert result.forecast is None
        assert "no targeted_ad_unit_ids" in result.error.lower() or "ad unit" in result.error.lower()

    def test_null_available_units_returns_error_no_forecast(self):
        response = MagicMock()
        response.availableUnits = None
        manager, _ = _build_manager(response)
        product = MagicMock(
            implementation_config={
                "targeted_ad_unit_ids": ["ad_unit_1"],
                "line_item_type": "STANDARD",
            }
        )

        result = manager.get_for_product(product)
        assert result.forecast is None
        assert result.error is not None

    def test_success_carries_spec_compliant_forecast(self):
        response = MagicMock()
        response.availableUnits = 75_000
        manager, _ = _build_manager(response)
        product = MagicMock(
            implementation_config={
                "targeted_ad_unit_ids": ["ad_unit_1"],
                "line_item_type": "STANDARD",
            }
        )

        result = manager.get_for_product(product)
        assert result.error is None
        assert result.forecast["method"] == "estimate"
        assert result.forecast["points"][0]["metrics"]["impressions"]["mid"] == 75000.0


class TestForecastResultSerialisation:
    def test_to_dict_emits_wire_shape(self):
        result = ForecastResult(
            forecast={"method": "estimate", "points": [{"label": "x", "metrics": {}}], "currency": "USD"},
            error=None,
            window_start="2026-05-08",
            window_end="2026-05-15",
            fetched_at="2026-05-08T12:00:00+00:00",
        )
        wire = result.to_dict()
        assert wire["window_start"] == "2026-05-08"
        assert wire["forecast"]["method"] == "estimate"
        assert wire["error"] is None
