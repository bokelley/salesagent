"""Tests for the FreeWheel adapter — factory wiring + dry-run + client construction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

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
    principal.get_adapter_id.return_value = "advertiser_42"
    principal.platform_mappings = {"freewheel": {"advertiser_id": "advertiser_42"}}
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

    def test_live_mode_create_returns_pending_credentials(self, mock_principal, sample_request, sample_packages):
        """Live mode create_media_buy is stubbed until the v3 commercial write
        flow is wired through the adapter (next PR)."""
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "pending_credentials"

    def test_live_mode_requires_api_token(self, mock_principal):
        with pytest.raises(ValueError, match="api_token"):
            FreeWheelAdapter(config={}, principal=mock_principal, dry_run=False, tenant_id="tenant_fw_1")


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
