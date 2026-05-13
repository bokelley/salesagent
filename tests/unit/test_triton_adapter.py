"""Tests for the Triton TAP adapter — factory wiring + dry-run behavior + JWT refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.adapters import get_adapter_default_channels, get_adapter_schemas
from src.adapters.triton import TritonAdapter, TritonAPIError, TritonClient
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage
from tests.factories.spec_required_kwargs import required_request_kwargs
from tests.helpers.adapter_test_helpers import invoke_create_media_buy


@pytest.fixture
def mock_principal():
    principal = MagicMock()
    principal.name = "test_principal"
    principal.principal_id = "principal_123"
    principal.get_adapter_id.return_value = "adv_42"
    principal.platform_mappings = {"triton": {"advertiser_id": "adv_42"}}
    return principal


@pytest.fixture
def sample_request():
    from tests.helpers.adcp_factories import create_test_package_request

    start = datetime.now(UTC)
    return CreateMediaBuyRequest(
        **required_request_kwargs(),
        brand={"domain": "audio.example.com"},
        packages=[create_test_package_request(product_id="prod_1")],
        start_time=start,
        end_time=start + timedelta(days=14),
    )


@pytest.fixture
def sample_packages():
    return [
        MediaPackage(
            package_id="pkg_audio_1",
            name="Morning Drive",
            delivery_type="guaranteed",
            impressions=100_000,
            format_ids=[FormatId(agent_url="https://test.com", id="audio_30s")],
        )
    ]


class TestRegistry:
    """Triton is parked while its APIs aren't production-ready — deregistered
    from ``ADAPTER_REGISTRY``. These tests pin the parked behaviour so
    re-introducing the entry has a fixed point to flip against.

    The adapter source under ``src/adapters/triton/`` is preserved, so the
    rest of the file's direct-construction tests (TestAdapterDryRun etc.)
    still exercise the adapter — just not via the registry."""

    def test_triton_not_registered(self):
        """``get_adapter_schemas`` returns ``None`` for unregistered adapter types.
        Both the canonical name and the alias must miss."""
        assert get_adapter_schemas("triton") is None
        assert get_adapter_schemas("triton_digital") is None

    def test_default_channels_empty_when_deregistered(self):
        """``get_adapter_default_channels`` returns an empty list for any
        adapter type not in the registry — surfaces the parked state at
        every channel-resolution call site."""
        assert get_adapter_default_channels("triton") == []


class TestAdapterDryRun:
    def test_dry_run_creates_buy_without_calling_client(self, mock_principal, sample_request, sample_packages):
        adapter = TritonAdapter(
            config={"username": "u", "password": "p"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert response.packages is not None
        assert len(response.packages) == 1
        assert response.packages[0].package_id == "pkg_audio_1"
        assert adapter._client is None

    def test_dry_run_rejects_unsupported_targeting(self, mock_principal, sample_request, sample_packages):
        from src.core.schemas import Targeting

        sample_packages[0] = sample_packages[0].model_copy(
            update={"targeting_overlay": Targeting(media_type_any_of=["olv"])}
        )
        adapter = TritonAdapter(
            config={"username": "u", "password": "p"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "unsupported_targeting"

    def test_live_mode_requires_credentials(self, mock_principal):
        with pytest.raises(ValueError, match="username"):
            TritonAdapter(config={}, principal=mock_principal, dry_run=False, tenant_id="tenant_1")


class TestClientJWTRefresh:
    @patch("src.adapters.triton.client.requests.post")
    @patch("src.adapters.triton.client.requests.request")
    def test_401_triggers_login_refresh_and_retry(self, mock_request, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "fresh-jwt"}, content=b'{"access_token":"fresh-jwt"}'
        )
        first = MagicMock(status_code=401, ok=False, content=b"")
        second = MagicMock(status_code=200, ok=True, json=lambda: {"id": "camp_1"}, content=b'{"id":"camp_1"}')
        mock_request.side_effect = [first, second]

        client = TritonClient(username="u", password="p")
        client._jwt = "stale-jwt"  # pretend we already have one
        result = client.create_campaign("adv_42", {"name": "x"})

        assert result == {"id": "camp_1"}
        assert mock_post.call_count == 1  # one re-login on 401
        assert mock_request.call_count == 2

    @patch("src.adapters.triton.client.requests.post")
    def test_login_failure_raises_api_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, text="invalid creds", content=b"invalid")
        client = TritonClient(username="bad", password="bad")
        with pytest.raises(TritonAPIError, match="login failed"):
            client.login()

    @patch("src.adapters.triton.client.requests.post")
    def test_password_auth_posts_username_password_body(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "jwt"}, content=b'{"access_token":"jwt"}'
        )
        client = TritonClient(username="alice@example.com", password="hunter2", auth_type="password")
        client.login()
        body = mock_post.call_args.kwargs["json"]
        assert body == {"username": "alice@example.com", "password": "hunter2"}

    @patch("src.adapters.triton.client.requests.post")
    def test_oauth_client_credentials_auth_posts_form_encoded_body(self, mock_post):
        """RFC 6749 §4.4: client_credentials grants use form-encoded bodies, not JSON."""
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "jwt"}, content=b'{"access_token":"jwt"}'
        )
        client = TritonClient(
            username="client-id-abc", password="client-secret-xyz", auth_type="oauth_client_credentials"
        )
        client.login()
        # Posted as form-encoded `data=`, not JSON `json=`
        assert "json" not in mock_post.call_args.kwargs
        body = mock_post.call_args.kwargs["data"]
        assert body == {
            "grant_type": "client_credentials",
            "client_id": "client-id-abc",
            "client_secret": "client-secret-xyz",
        }
