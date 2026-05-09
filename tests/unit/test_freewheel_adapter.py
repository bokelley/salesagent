"""Tests for the FreeWheel adapter — factory wiring + dry-run + OAuth refresh."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.adapters import get_adapter_default_channels, get_adapter_schemas
from src.adapters.freewheel import FreeWheelAdapter, FreeWheelAPIError, FreeWheelClient
from src.adapters.freewheel.schemas import FreeWheelConnectionConfig, FreeWheelProductConfig
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage
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
            config={"client_id": "cid", "client_secret": "csec", "network_id": "12345"},
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
            config={"client_id": "cid", "client_secret": "csec", "network_id": "12345"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "unsupported_targeting"

    def test_live_mode_create_returns_pending_credentials(self, mock_principal, sample_request, sample_packages):
        """Live mode is intentionally stubbed until staging credentials land."""
        adapter = FreeWheelAdapter(
            config={"client_id": "cid", "client_secret": "csec", "network_id": "12345"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "pending_credentials"

    def test_live_mode_requires_credentials(self, mock_principal):
        with pytest.raises(ValueError, match="client_id"):
            FreeWheelAdapter(config={}, principal=mock_principal, dry_run=False, tenant_id="tenant_fw_1")


class TestClientOAuth:
    @patch("src.adapters.freewheel.client.requests.post")
    @patch("src.adapters.freewheel.client.requests.request")
    def test_token_fetch_caches_with_expiry(self, mock_request, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "tok-1", "expires_in": 7 * 24 * 60 * 60},
            content=b'{"access_token":"tok-1"}',
        )
        mock_request.return_value = MagicMock(status_code=200, ok=True, json=lambda: {"id": "n1"}, content=b"{}")

        client = FreeWheelClient(
            client_id="cid", client_secret="csec", network_id="12345", base_url="https://api.stg.freewheel.tv"
        )
        client.get_network()

        # Token cached, second call shouldn't re-fetch
        client.get_network()
        assert mock_post.call_count == 1
        assert client._token == "tok-1"
        assert client._token_expires_at > time.time()

    @patch("src.adapters.freewheel.client.requests.post")
    @patch("src.adapters.freewheel.client.requests.request")
    def test_401_triggers_refresh_and_retry(self, mock_request, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fresh", "expires_in": 7 * 24 * 60 * 60},
            content=b"",
        )
        first = MagicMock(status_code=401, ok=False, content=b"")
        second = MagicMock(status_code=200, ok=True, json=lambda: {"id": "c1"}, content=b'{"id":"c1"}')
        mock_request.side_effect = [first, second]

        client = FreeWheelClient(
            client_id="cid", client_secret="csec", network_id="12345", base_url="https://api.stg.freewheel.tv"
        )
        client._token = "stale"
        client._token_expires_at = time.time() + 1000
        result = client.create_campaign({"name": "x"})

        assert result == {"id": "c1"}
        assert mock_post.call_count == 1  # one re-auth on 401
        assert mock_request.call_count == 2

    @patch("src.adapters.freewheel.client.requests.post")
    def test_auth_failure_raises_api_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, text="invalid client", content=b"")
        client = FreeWheelClient(
            client_id="bad", client_secret="bad", network_id="0", base_url="https://api.stg.freewheel.tv"
        )
        with pytest.raises(FreeWheelAPIError, match="auth failed"):
            client._fetch_token()
