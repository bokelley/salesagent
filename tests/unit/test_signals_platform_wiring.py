"""Signals protocol wiring on the modern ``core/`` platform."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from adcp.decisioning import create_adcp_server_from_platform

from core.main import AUTH_OPTIONAL_TOOLS
from core.platforms.gam import GamPlatform
from core.platforms.mock import MockSellerPlatform
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetSignalsRequest
from src.core.tools.signals import _get_signals_impl


def _advertised_tools(platform) -> frozenset[str]:
    handler, executor, _registry = create_adcp_server_from_platform(
        platform,
        auto_emit_completion_webhooks=False,
        validate_at_init=False,
    )
    try:
        return handler.get_advertised_tools()
    finally:
        executor.shutdown(wait=True)


def test_get_signals_is_auth_optional_discovery_tool() -> None:
    assert "get_signals" in AUTH_OPTIONAL_TOOLS


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_platforms_advertise_owned_signal_discovery_only(platform) -> None:
    advertised = _advertised_tools(platform)
    assert "get_signals" in advertised
    assert "activate_signal" not in advertised


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_owned_signal_platforms_do_not_expose_activation(platform) -> None:
    assert not hasattr(platform, "activate_signal")


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_platforms_declare_signals_protocol(platform) -> None:
    protocols = {protocol.value for protocol in platform.capabilities.supported_protocols}
    assert "signals" in protocols


@pytest.mark.parametrize("platform", [MockSellerPlatform(), GamPlatform()])
def test_platforms_declare_catalog_signals_capability(platform) -> None:
    assert platform.capabilities.signals is not None
    assert platform.capabilities.signals.features is not None
    assert platform.capabilities.signals.features.catalog_signals is True


@pytest.mark.asyncio
async def test_get_signals_filters_by_structured_signal_id() -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(
        signal_ids=[
            {
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "auto_intenders_q1_2025",
            }
        ]
    )

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert [signal.signal_agent_segment_id for signal in response.signals] == ["auto_intenders_q1_2025"]


@pytest.mark.asyncio
async def test_get_signals_matches_natural_language_signal_spec_tokens() -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(signal_spec="Adults interested in electric vehicles")

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert response.signals
    assert response.signals[0].signal_agent_segment_id == "auto_intenders_q1_2025"


@pytest.mark.asyncio
@pytest.mark.parametrize("signal_spec", ["EV", "AI", "adults"])
async def test_get_signals_short_or_stopword_specs_do_not_match_all(signal_spec: str) -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(signal_spec=signal_spec)

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert len(response.signals) < 6


@pytest.mark.asyncio
async def test_get_signals_supports_pagination() -> None:
    identity = ResolvedIdentity(
        tenant_id="tenant_1",
        tenant={"ad_server": "google_ad_manager"},
        protocol="mcp",
    )
    req = GetSignalsRequest(pagination={"max_results": 2})

    with patch("src.core.tools.signals._load_tenant_signals", return_value=[]):
        response = await _get_signals_impl(req, identity)

    assert len(response.signals) == 2
    assert response.pagination is not None
    assert response.pagination.has_more is True
    assert response.pagination.cursor == "2"
