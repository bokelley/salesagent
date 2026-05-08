"""Tests for the pre-flight overbook detector on create_media_buy.

Covers tescoboy issue #152 (single-product Phase 1).

The detector compares the buyer's implied impression goal
(``budget / cpm * 1000``) against GAM's availability forecast and
returns a list of advisory warnings. Warnings are surfaced to the
buyer via ``response.ext.warnings`` per AdCP extension convention,
pending the upstream RFC at:
https://github.com/adcontextprotocol/adcp/issues/4248

The detector is **fail-open everywhere**: it never blocks the buy.
Any branch that can't reach a clean comparison (non-GAM adapter,
multi-product buy, missing rate, missing ad units, missing flight
dates, GAM forecast error) returns ``[]``.
"""

from datetime import date
from unittest.mock import MagicMock, patch

from src.core.tools.media_buy_create import _detect_overbook_warnings


def _adapter(kind: str = "GoogleAdManager", available_units: int | None = None):
    """Build an adapter with a mock GAMForecastManager wired in."""
    forecast_service = MagicMock()
    response = MagicMock()
    response.availableUnits = available_units
    forecast_service.getAvailabilityForecast.return_value = response

    client_manager = MagicMock()
    client_manager.get_service.return_value = forecast_service

    orders_manager = MagicMock()
    orders_manager.client_manager = client_manager
    orders_manager.advertiser_id = "adv_1"

    adapter = MagicMock()
    adapter.__class__.__name__ = kind
    adapter.orders_manager = orders_manager
    return adapter


def _product(product_id="prod_1", cpm=10.0):
    """Build a Product with one CPM pricing option."""
    pricing_option = MagicMock()
    pricing_option.root = MagicMock(pricing_model="cpm", rate=cpm, currency="USD", is_fixed=True)
    product = MagicMock()
    product.product_id = product_id
    product.pricing_options = [pricing_option]
    return product


def _req(start_date=date(2026, 5, 7), end_date=date(2026, 5, 14)):
    """Build a request with flight dates."""
    req = MagicMock()
    req.flight_start_date = start_date
    req.flight_end_date = end_date
    req.start_time = None
    req.end_time = None
    return req


class TestOverbookDetected:
    def test_goal_above_forecast_emits_warning(self):
        # $1000 @ $12.50 CPM = 80,000 implied impressions.
        # Forecast: 11,428 available. ~7x overbook (matches the issue's
        # 2026-05-07 evidence).
        adapter = _adapter(available_units=11_428)
        product = _product(cpm=12.50)
        configs = {"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}}

        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs=configs,
            req=_req(),
            total_budget=1000.0,
        )

        assert len(warnings) == 1
        warning = warnings[0]
        assert warning["code"] == "inventory_overbook_minor"
        assert warning["details"]["goal_impressions"] == 80_000
        assert warning["details"]["forecast_available_impressions"] == 11_428
        assert warning["details"]["overbook_percent"] >= 600
        assert warning["details"]["product_id"] == "prod_1"

    def test_warning_message_includes_numbers_and_explanation(self):
        adapter = _adapter(available_units=50_000)
        product = _product(cpm=10.0)
        configs = {"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}}

        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs=configs,
            req=_req(),
            total_budget=1000.0,
        )

        # $1000 @ $10 CPM = 100,000 implied; forecast 50,000 → 100% overbook
        msg = warnings[0]["message"]
        assert "100,000" in msg
        assert "50,000" in msg
        assert "INVENTORY_RELEASED" in msg


class TestNoOverbookNoWarning:
    def test_goal_below_forecast_emits_nothing(self):
        adapter = _adapter(available_units=100_000)
        product = _product(cpm=10.0)
        configs = {"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}}

        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs=configs,
            req=_req(),
            total_budget=500.0,  # 50,000 implied — well under 100,000 forecast
        )
        assert warnings == []

    def test_goal_exactly_at_forecast_emits_nothing(self):
        # Boundary: implied == available is "fits in forecast"; warning
        # only fires when goal > available.
        adapter = _adapter(available_units=100_000)
        product = _product(cpm=10.0)
        configs = {"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}}

        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs=configs,
            req=_req(),
            total_budget=1000.0,  # exactly 100,000 implied
        )
        assert warnings == []


class TestFailOpenBranches:
    """Every short-circuit must return [], never raise, never block."""

    def test_non_gam_adapter_skips_detection(self):
        adapter = _adapter(kind="MockAdServer")
        product = _product()
        configs = {"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}}

        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs=configs,
            req=_req(),
            total_budget=1_000_000.0,  # would obviously overbook if checked
        )
        assert warnings == []

    def test_multi_product_buy_skips_detection(self):
        # Multi-product budget allocation deferred per #152 design.
        adapter = _adapter(available_units=100)
        configs = {
            "prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]},
            "prod_2": {"targeted_ad_unit_ids": ["ad_unit_2"]},
        }
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[_product("prod_1"), _product("prod_2")],
            effective_configs=configs,
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_zero_budget_skips_detection(self):
        adapter = _adapter(available_units=100)
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[_product()],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=_req(),
            total_budget=0.0,
        )
        assert warnings == []

    def test_no_ad_units_skips_detection(self):
        adapter = _adapter(available_units=100)
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[_product()],
            effective_configs={"prod_1": {}},  # no targeted_ad_unit_ids
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_non_cpm_pricing_skips_detection(self):
        # The implied-impressions math only makes sense for CPM. CPC,
        # flat-rate, etc. require different conversions; defer to follow-up.
        adapter = _adapter(available_units=100)
        product = MagicMock()
        product.product_id = "prod_1"
        cpc_option = MagicMock()
        cpc_option.root = MagicMock(pricing_model="cpc", rate=2.50)
        product.pricing_options = [cpc_option]

        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_zero_rate_skips_detection(self):
        adapter = _adapter(available_units=100)
        product = _product(cpm=0.0)
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_no_pricing_options_skips_detection(self):
        adapter = _adapter(available_units=100)
        product = MagicMock()
        product.product_id = "prod_1"
        product.pricing_options = []
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_missing_flight_dates_skips_detection(self):
        adapter = _adapter(available_units=100)
        req = MagicMock()
        req.flight_start_date = None
        req.flight_end_date = None
        req.start_time = None
        req.end_time = None
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[_product()],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=req,
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_forecast_returns_none_skips_warning(self):
        # GAM rejected the forecast call (NO_FORECAST_YET, network, etc).
        # Buy proceeds without warning — fail-open.
        adapter = _adapter(available_units=None)
        product = _product()
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[product],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []

    def test_orders_manager_missing_skips_detection(self):
        # Some adapter paths don't expose orders_manager (mock, dry-run
        # cases). Detector must fail-open rather than crash.
        adapter = MagicMock()
        adapter.__class__.__name__ = "GoogleAdManager"
        adapter.orders_manager = None
        warnings = _detect_overbook_warnings(
            adapter=adapter,
            products_in_buy=[_product()],
            effective_configs={"prod_1": {"targeted_ad_unit_ids": ["ad_unit_1"]}},
            req=_req(),
            total_budget=1_000_000.0,
        )
        assert warnings == []


class TestForecastManagerInvocation:
    def test_forecast_call_uses_product_implementation_config(self):
        # Verifies the line_item_type and include_descendants flags from
        # impl_config thread through to the forecast manager.
        adapter = _adapter(available_units=100_000)
        product = _product()
        configs = {
            "prod_1": {
                "targeted_ad_unit_ids": ["ad_unit_1", "ad_unit_2"],
                "line_item_type": "SPONSORSHIP",
                "include_descendants": False,
            }
        }

        with patch(
            "src.adapters.gam.managers.forecast.GAMForecastManager.get_available_units",
            return_value=100_000,
        ) as mock_call:
            _detect_overbook_warnings(
                adapter=adapter,
                products_in_buy=[product],
                effective_configs=configs,
                req=_req(),
                total_budget=1000.0,
            )

        mock_call.assert_called_once()
        kwargs = mock_call.call_args.kwargs
        assert kwargs["ad_unit_ids"] == ["ad_unit_1", "ad_unit_2"]
        assert kwargs["line_item_type"] == "SPONSORSHIP"
        assert kwargs["include_descendants"] is False
