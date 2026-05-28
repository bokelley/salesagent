"""Unit tests for pricing model validation (AdCP PR #88)."""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.schemas import PricingModel
from src.core.tools.media_buy_create import (
    _collect_package_pricing_info_by_index,
    _derive_single_request_currency,
    _validate_pricing_model_selection,
)


def _fixed_pricing_option(pricing_model: str = "cpm", currency: str = "USD", rate: str = "10.00") -> Mock:
    pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package"])
    pricing_option.pricing_model = pricing_model
    pricing_option.currency = currency
    pricing_option.is_fixed = True
    pricing_option.rate = Decimal(rate)
    pricing_option.min_spend_per_package = None
    return pricing_option


def _package(product_id: str, pricing_option_id: str | None, pricing_model: PricingModel | None = None) -> Mock:
    package = Mock()
    package.package_id = None
    package.product_id = product_id
    package.budget = 5000.0
    package.pricing_option_id = pricing_option_id
    package.pricing_model = pricing_model
    package.bid_price = None
    return package


class TestPricingValidation:
    """Test pricing model validation logic."""

    def test_legacy_product_without_pricing_model_in_package(self):
        """Test product with no pricing_options should raise data integrity error."""
        # Since pricing_options is now required, products without them trigger data integrity errors
        product = Mock()
        product.product_id = "legacy_product"
        product.pricing_options = []  # No pricing options = data integrity error

        # Package doesn't specify pricing_model (Mock with necessary attributes)
        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "legacy_product"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = None
        package.bid_price = None

        # Should raise data integrity error
        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "has no pricing_options configured" in str(exc_info.value)
        assert "data integrity error" in str(exc_info.value)

    def test_legacy_product_with_pricing_model_in_package_should_error(self):
        """Test product with no pricing_options should raise data integrity error."""
        # Since pricing_options is now required, products without them trigger data integrity errors
        product = Mock()
        product.product_id = "legacy_product"
        product.pricing_options = []  # No pricing options = data integrity error

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "legacy_product"
        package.pricing_model = PricingModel.cpcv
        package.budget = 5000.0
        package.pricing_option_id = None
        package.bid_price = None

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "has no pricing_options configured" in str(exc_info.value)
        assert "data integrity error" in str(exc_info.value)

    def test_new_product_with_matching_pricing_model(self):
        """Test product with pricing_options and package specifying valid pricing_model."""
        # Setup pricing option - use spec to prevent auto-creating .root attribute
        # (adcp 2.14.0+ uses RootModel wrapper, but mocks should not have .root)
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package"])
        pricing_option.pricing_model = "cpcv"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True
        pricing_option.rate = Decimal("0.25")
        pricing_option.min_spend_per_package = None

        product = Mock()
        product.product_id = "video_product"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "video_product"
        package.budget = 10000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpcv
        package.bid_price = None

        result = _validate_pricing_model_selection(package, product, "USD")

        assert result["pricing_model"] == "cpcv"
        assert result["rate"] == 0.25
        assert result["currency"] == "USD"
        assert result["is_fixed"] is True

    def test_pricing_model_not_offered_by_product(self):
        """Test package requesting pricing_model not offered by product."""
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed"])
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True

        product = Mock()
        product.product_id = "display_product"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "display_product"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpp
        package.bid_price = None

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "does not offer pricing model" in str(exc_info.value)
        assert "cpp" in str(exc_info.value).lower()

    def test_currency_mismatch(self):
        """Test package with campaign currency that doesn't match pricing option currency."""
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed"])
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpm
        package.bid_price = None

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "EUR")

        assert "does not offer pricing model" in str(exc_info.value)
        assert "EUR" in str(exc_info.value)

    def test_auction_pricing_without_bid_price(self):
        """Test auction-based pricing without bid_price in package."""
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "price_guidance"])
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = False
        pricing_option.price_guidance = {"floor": 10.0}

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpm
        package.bid_price = None

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        # Error message is the first argument
        error_str = str(exc_info.value)
        assert "bid_price" in error_str and "requires" in error_str

    def test_bid_price_below_floor(self):
        """Test bid_price below floor price."""
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "price_guidance"])
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = False
        pricing_option.price_guidance = {"floor": 15.0}

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpm
        package.bid_price = 10.0

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "below floor price" in str(exc_info.value)

    def test_fixed_pricing_without_rate(self):
        """Test fixed pricing option without rate specified (invalid)."""
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate"])
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True
        pricing_option.rate = None  # Invalid - fixed pricing needs rate

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpm
        package.bid_price = None

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "no rate specified" in str(exc_info.value)

    def test_budget_below_minimum_spend(self):
        """Test package budget below min_spend_per_package."""
        pricing_option = Mock(spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package"])
        pricing_option.pricing_model = "cpcv"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = True
        pricing_option.rate = Decimal("0.30")
        pricing_option.min_spend_per_package = Decimal("10000.00")

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpcv
        package.bid_price = None

        with pytest.raises(AdCPValidationError) as exc_info:
            _validate_pricing_model_selection(package, product, "USD")

        assert "below minimum spend" in str(exc_info.value)

    def test_valid_auction_pricing_with_bid(self):
        """Test valid auction pricing with bid_price >= floor."""
        pricing_option = Mock(
            spec=["pricing_model", "currency", "is_fixed", "rate", "price_guidance", "min_spend_per_package"]
        )
        pricing_option.pricing_model = "cpm"
        pricing_option.currency = "USD"
        pricing_option.is_fixed = False
        pricing_option.rate = None
        pricing_option.price_guidance = {"floor": 10.0, "p50": 15.0}
        pricing_option.min_spend_per_package = None

        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [pricing_option]

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "product_1"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = PricingModel.cpm
        package.bid_price = 18.0

        result = _validate_pricing_model_selection(package, product, "USD")

        assert result["pricing_model"] == "cpm"
        assert result["is_fixed"] is False
        assert result["bid_price"] == 18.0

    def test_request_currency_comes_from_selected_pricing_option(self):
        """Selected pricing_option_id, not first product option, determines currency."""
        product = Mock()
        product.product_id = "product_1"
        product.pricing_options = [
            _fixed_pricing_option(currency="USD"),
            _fixed_pricing_option(currency="EUR"),
        ]

        package_pricing_info = _collect_package_pricing_info_by_index(
            packages=[_package("product_1", "cpm_eur_fixed")],
            product_map={"product_1": product},
        )

        assert package_pricing_info[0]["currency"] == "EUR"
        assert _derive_single_request_currency(package_pricing_info) == "EUR"

    def test_mixed_selected_currencies_are_rejected(self):
        """A media buy cannot combine packages selected in different currencies."""
        usd_product = Mock()
        usd_product.product_id = "usd_product"
        usd_product.pricing_options = [_fixed_pricing_option(currency="USD")]

        eur_product = Mock()
        eur_product.product_id = "eur_product"
        eur_product.pricing_options = [_fixed_pricing_option(currency="EUR")]

        package_pricing_info = _collect_package_pricing_info_by_index(
            packages=[
                _package("usd_product", "cpm_usd_fixed"),
                _package("eur_product", "cpm_eur_fixed"),
            ],
            product_map={"usd_product": usd_product, "eur_product": eur_product},
        )

        with pytest.raises(ValueError, match="same currency"):
            _derive_single_request_currency(package_pricing_info)

    def test_legacy_campaign_currency_disambiguates_pricing_model_selection(self):
        """Legacy currency fields still steer pricing_model fallback selection."""
        usd_first_product = Mock()
        usd_first_product.product_id = "usd_first_product"
        usd_first_product.pricing_options = [
            _fixed_pricing_option(currency="USD"),
            _fixed_pricing_option(currency="EUR"),
        ]

        eur_first_product = Mock()
        eur_first_product.product_id = "eur_first_product"
        eur_first_product.pricing_options = [
            _fixed_pricing_option(currency="EUR"),
            _fixed_pricing_option(currency="USD"),
        ]

        package_pricing_info = _collect_package_pricing_info_by_index(
            packages=[
                _package("usd_first_product", None, PricingModel.cpm),
                _package("eur_first_product", None, PricingModel.cpm),
            ],
            product_map={
                "usd_first_product": usd_first_product,
                "eur_first_product": eur_first_product,
            },
            campaign_currency="USD",
        )

        assert package_pricing_info[0]["currency"] == "USD"
        assert package_pricing_info[1]["currency"] == "USD"
        assert _derive_single_request_currency(package_pricing_info) == "USD"

    def test_legacy_conversion_uses_campaign_currency_before_first_option(self):
        """Legacy product_ids conversion honors request currency before product option order."""
        product = Mock()
        product.product_id = "legacy_product"
        product.pricing_options = [
            _fixed_pricing_option(currency="USD"),
            _fixed_pricing_option(currency="EUR"),
        ]

        package_pricing_info = _collect_package_pricing_info_by_index(
            packages=[_package("legacy_product", "legacy_conversion")],
            product_map={"legacy_product": product},
            campaign_currency="EUR",
        )

        assert package_pricing_info[0]["currency"] == "EUR"

    def test_legacy_pricing_model_without_currency_rejects_multi_currency_options(self):
        """Legacy pricing_model cannot silently pick from multiple currencies."""
        product = Mock()
        product.product_id = "legacy_product"
        product.pricing_options = [
            _fixed_pricing_option(currency="USD"),
            _fixed_pricing_option(currency="EUR"),
        ]

        with pytest.raises(AdCPValidationError, match="multiple currencies"):
            _collect_package_pricing_info_by_index(
                packages=[_package("legacy_product", None, PricingModel.cpm)],
                product_map={"legacy_product": product},
            )

    def test_product_with_no_pricing_information(self):
        """Test product with no pricing_options should raise data integrity error."""
        # Since pricing_options is now required, products without them trigger data integrity errors
        product = Mock()
        product.product_id = "broken_product"
        product.pricing_options = []  # No pricing options = data integrity error

        package = Mock()
        package.package_id = "pkg_1"
        package.product_id = "broken_product"
        package.budget = 5000.0
        package.pricing_option_id = None
        package.pricing_model = None
        package.bid_price = None
