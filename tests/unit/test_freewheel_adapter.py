"""Tests for the FreeWheel adapter — factory wiring + dry-run + client construction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, MagicMock

import pytest

from src.adapters import get_adapter_default_channels, get_adapter_schemas
from src.adapters.freewheel import FreeWheelAdapter, FreeWheelClient
from src.adapters.freewheel.schemas import FreeWheelConnectionConfig, FreeWheelProductConfig
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage
from tests.factories.spec_required_kwargs import required_request_kwargs
from tests.helpers.adapter_test_helpers import invoke_create_media_buy


@pytest.fixture
def mock_principal():
    principal = MagicMock()
    principal.name = "video_advertiser"
    principal.principal_id = "principal_fw_1"
    # FW advertiser IDs are integers in the live API; tests use a numeric
    # string so the adapter can cast cleanly when calling create_campaign.
    principal.get_adapter_id.return_value = "1356511"
    principal.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}
    return principal


@pytest.fixture
def sample_request():
    from tests.helpers.adcp_factories import create_test_package_request

    start = datetime.now(UTC)
    return CreateMediaBuyRequest(
        **required_request_kwargs(),
        brand={"domain": "brand.example.com"},
        packages=[create_test_package_request(product_id="prod_video_1")],
        start_time=start,
        end_time=start + timedelta(days=14),
    )


@pytest.fixture
def sample_packages():
    return [
        MediaPackage(
            package_id="pkg_video_1",
            name="Pre-roll Bundle",
            delivery_type="guaranteed",
            impressions=500_000,
            format_ids=[FormatId(agent_url="https://test.com", id="video_15s")],
        )
    ]


class TestRegistry:
    def test_get_adapter_schemas_returns_freewheel_classes(self):
        schemas = get_adapter_schemas("freewheel")
        assert schemas is not None
        assert schemas.connection_config is FreeWheelConnectionConfig
        assert schemas.product_config is FreeWheelProductConfig
        assert schemas.capabilities.inventory_entity_label == "Placements"

    def test_default_channels_emphasise_video(self):
        channels = get_adapter_default_channels("freewheel")
        assert "olv" in channels
        assert "ctv" in channels


class TestAdapterDryRun:
    def test_dry_run_creates_buy_without_calling_client(self, mock_principal, sample_request, sample_packages):
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert response.packages is not None
        assert len(response.packages) == 1
        assert adapter._client is None

    def test_dry_run_rejects_postal_targeting(self, mock_principal, sample_request, sample_packages):
        from src.core.schemas import Targeting

        sample_packages[0] = sample_packages[0].model_copy(
            update={
                "targeting_overlay": Targeting(
                    geo_countries=["US"],
                    geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
                )
            }
        )
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "unsupported_targeting"

    def test_live_mode_requires_api_token(self, mock_principal):
        with pytest.raises(ValueError, match="api_token"):
            FreeWheelAdapter(config={}, principal=mock_principal, dry_run=False, tenant_id="tenant_fw_1")


class TestLiveCreateMediaBuy:
    """Mapping A: AdCP MediaBuy -> FW IO, AdCP Package -> FW Placement,
    with FW Campaign as a per-buy wrapper above the IO."""

    def _adapter_with_mock_client(self, mock_principal, **client_overrides):
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()
        # Default happy-path return values; tests can override.
        adapter._client.commercial.create_campaign.return_value = MagicMock(id=900001)
        adapter._client.commercial.create_insertion_order.return_value = MagicMock(id=900002)
        adapter._client.commercial.create_placement.return_value = MagicMock(id=900003)
        for attr, value in client_overrides.items():
            setattr(adapter._client.commercial, attr, value)
        return adapter

    def test_creates_campaign_io_and_placement_per_package(self, mock_principal, sample_request, sample_packages):
        adapter = self._adapter_with_mock_client(mock_principal)

        response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        # Campaign + IO each created once. Names are derived from po_number /
        # timestamp (unpredictable from the test's POV) so we use ANY there,
        # but the ID linkage and advertiser scoping are exact.
        adapter._client.commercial.create_campaign.assert_called_once_with(name=ANY, advertiser_id=1356511)
        adapter._client.commercial.create_insertion_order.assert_called_once_with(name=ANY, campaign_id=900001)

        # One placement per AdCP package, all parented to the new IO.
        assert adapter._client.commercial.create_placement.call_count == len(sample_packages)
        for package, call in zip(
            sample_packages, adapter._client.commercial.create_placement.call_args_list, strict=True
        ):
            assert call == ((), {"name": package.name, "insertion_order_id": 900002})

        # media_buy_id reflects the IO (not the Campaign) — IO is the
        # commercial transaction in Mapping A.
        assert response.media_buy_id == "freewheel_900002"

    def test_upstream_error_returns_error_response(self, mock_principal, sample_request, sample_packages):
        from src.adapters.freewheel import FreeWheelError

        bad_create = MagicMock(side_effect=FreeWheelError("network 503", status_code=503, body="oops"))
        adapter = self._adapter_with_mock_client(mock_principal, create_campaign=bad_create)

        response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        assert hasattr(response, "errors")
        assert response.errors[0].code == "upstream_error"
        # No IO/placement created if campaign failed.
        adapter._client.commercial.create_insertion_order.assert_not_called()
        adapter._client.commercial.create_placement.assert_not_called()


class TestCheckMediaBuyStatus:
    def test_live_mode_reads_insertion_order_stage(self, mock_principal):
        from datetime import UTC, datetime

        adapter = FreeWheelAdapter(
            config={"api_token": "t"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()
        adapter._client.commercial.get_insertion_order.return_value = MagicMock(stage="BOOKED", status=None)

        result = adapter.check_media_buy_status("freewheel_900002", today=datetime.now(UTC))

        adapter._client.commercial.get_insertion_order.assert_called_once_with(900002)
        assert result.status == "booked"


class TestClientConstruction:
    def test_client_composes_inventory_and_commercial(self):
        client = FreeWheelClient(api_token="test-bearer-token", base_url="https://api.stg.freewheel.tv")
        assert client.inventory is not None
        assert client.commercial is not None

    def test_client_token_info_calls_auth_endpoint(self):
        """token_info() proves the bearer is valid; uses /auth/token/info."""
        from src.adapters.freewheel._transport import FreeWheelTransport

        mock_session = MagicMock()
        mock_session.request.return_value = MagicMock(
            status_code=200,
            ok=True,
            content=b'{"user_id": 0, "expires_in": 604800, "created_at": 1700000000}',
            text='{"user_id": 0, "expires_in": 604800, "created_at": 1700000000}',
            json=lambda: {"user_id": 0, "expires_in": 604800, "created_at": 1700000000},
        )
        transport = FreeWheelTransport(api_token="t", session=mock_session)
        info = transport.token_info()

        assert info["expires_in"] == 604800
        call_kwargs = mock_session.request.call_args.kwargs
        assert call_kwargs["url"].endswith("/auth/token/info")
        assert call_kwargs["headers"]["Authorization"] == "Bearer t"
        assert call_kwargs["headers"]["accept"] == "application/json"
