from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, PackageRequest, Principal

pytestmark = pytest.mark.unit

# Default agent URL for creating FormatId objects
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def make_format_id(format_id: str) -> FormatId:
    """Helper to create FormatId objects with default agent URL."""
    return FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id)


@pytest.fixture
def sample_packages():
    """A fixture to create a sample list of media packages for use in tests."""
    return [
        MediaPackage(
            package_id="pkg_1",
            name="Guaranteed Banner",
            delivery_type="guaranteed",
            impressions=333333,  # 5000 budget / 15 CPM * 1000
            budget=5000.0,  # Budget as float in MediaPackage (internal adapter format)
            format_ids=[
                make_format_id("display_300x250"),
                make_format_id("display_728x90"),
            ],
        )
    ]


def test_mock_ad_server_create_media_buy(sample_packages, mocker):
    """
    Tests that the MockAdServer correctly creates a media buy
    when a create_media_buy request is received.
    """
    # Arrange
    principal = Principal(
        principal_id="test_principal",
        name="Test Principal",
        platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
    )

    # Mock get_current_tenant to avoid database access in unit test
    mocker.patch("src.core.config_loader.get_current_tenant", return_value={"tenant_id": "test_tenant"})

    adapter = MockAdServer({}, principal, tenant_id="test_tenant")
    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(days=30)

    # Per AdCP v2.2.0: packages with budget are required
    packages = [
        PackageRequest(
            product_id="pkg_1",
            budget=5000.0,
            pricing_option_id="test_pricing",
            format_ids=[
                make_format_id("display_300x250"),
                make_format_id("display_728x90"),
            ],
        )
    ]

    request = CreateMediaBuyRequest(
        brand={"domain": "sports.example.com"},  # Required per AdCP spec
        packages=packages,  # AdCP v2.2.0: packages required
        start_time=start_time,
        end_time=end_time,
        po_number="PO-12345",
    )

    # Act
    response = adapter.create_media_buy(
        request=request, packages=sample_packages, start_time=start_time, end_time=end_time
    )

    # Assert
    assert response.media_buy_id == "buy_PO-12345"

    # Check the internal state of the mock server
    internal_buy = adapter._media_buys.get("buy_PO-12345")
    assert internal_buy is not None
    assert internal_buy["total_budget"] == 5000
    assert len(internal_buy["packages"]) == 1
    assert internal_buy["packages"][0].package_id == "pkg_1"


class TestResolvePricingRate:
    """Regression tests for AdServerAdapter._resolve_pricing_rate hard-fail behavior.

    Replaces the previous package.cpm fallback (dropped with MediaPackage.cpm).
    """

    @staticmethod
    def _pkg(package_id: str = "pkg_1") -> MediaPackage:
        return MediaPackage(
            package_id=package_id,
            name="Test",
            delivery_type="guaranteed",
            impressions=1000,
            format_ids=[make_format_id("display_300x250")],
        )

    def test_resolves_fixed_rate_from_pricing_info(self):
        from src.adapters.base import AdServerAdapter

        rate, rate_type = AdServerAdapter._resolve_pricing_rate(
            self._pkg(),
            {"pkg_1": {"pricing_model": "cpm", "rate": 12.5, "is_fixed": True, "bid_price": None}},
        )
        assert rate == 12.5
        assert rate_type == "CPM"

    def test_resolves_auction_rate_from_bid_price(self):
        from src.adapters.base import AdServerAdapter

        rate, rate_type = AdServerAdapter._resolve_pricing_rate(
            self._pkg(),
            {"pkg_1": {"pricing_model": "cpm", "rate": None, "is_fixed": False, "bid_price": 7.25}},
        )
        assert rate == 7.25
        assert rate_type == "CPM"

    def test_flat_rate_pricing_returns_flat_rate_type(self):
        from src.adapters.base import AdServerAdapter

        _rate, rate_type = AdServerAdapter._resolve_pricing_rate(
            self._pkg(),
            {"pkg_1": {"pricing_model": "flat_rate", "rate": 5000.0, "is_fixed": True, "bid_price": None}},
        )
        assert rate_type == "FLAT_RATE"

    def test_raises_when_pricing_info_missing(self):
        from src.adapters.base import AdServerAdapter

        with pytest.raises(ValueError, match="Missing pricing info"):
            AdServerAdapter._resolve_pricing_rate(self._pkg(), None)

        with pytest.raises(ValueError, match="Missing pricing info"):
            AdServerAdapter._resolve_pricing_rate(self._pkg(), {})

    def test_raises_when_auction_pricing_has_no_bid_price(self):
        from src.adapters.base import AdServerAdapter

        with pytest.raises(ValueError, match="auction pricing but has no bid_price"):
            AdServerAdapter._resolve_pricing_rate(
                self._pkg(),
                {"pkg_1": {"pricing_model": "cpm", "rate": None, "is_fixed": False, "bid_price": None}},
            )
