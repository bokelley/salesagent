"""``get_adcp_capabilities`` response patches.

Three amendments on the SDK's default capabilities response (see
``core.platforms._capabilities_envelope``):

1. Envelope ``status`` field — AdCP 3.0.11 protocol-envelope schema
   requires it. The upstream SDK doesn't emit it yet.
2. ``portfolio.publisher_domains`` — AdCP v3 moved publisher portfolio
   from the retired ``list_authorized_properties`` onto this response.
   Salesagent populates it per-tenant from ``PublisherPartner``.
3. ``webhook_signing`` — AdCP v3 exposes a native capability block for
   RFC 9421 webhook signing. Salesagent populates it from the tenant's
   active signing credential and advertises the legacy HMAC fallback.

This test pins all shims — if a future SDK revision adds them natively,
the assertions still pass against the SDK's output and we can drop the
workarounds (and these pin tests).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_shim_is_installed_on_platform_handler() -> None:
    """Importing the shim module installs the patch on
    ``PlatformHandler.get_adcp_capabilities`` — confirms the side-effect
    import in ``core/main.py`` is functional and won't silently drop.
    """
    # Side-effect import installs the patch.
    from adcp.decisioning.handler import PlatformHandler

    from core.platforms import _capabilities_envelope

    assert PlatformHandler.get_adcp_capabilities is _capabilities_envelope._get_adcp_capabilities_patched, (
        "shim not installed — get_adcp_capabilities responses will be missing status/portfolio"
    )


@pytest.mark.asyncio
async def test_status_appended_only_when_missing() -> None:
    """When the handler already emits ``status``, we don't clobber it."""

    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    # Stub the original to return a body that already has status.
    async def _original_with_status(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": [], "status": "working"}

    import core.platforms._capabilities_envelope as mod

    mod._ORIGINAL = _original_with_status
    try:
        result = await _get_adcp_capabilities_patched(object())
        assert result["status"] == "working", "must not clobber existing status"
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_status_completed_appended_when_absent() -> None:
    """When the handler emits a body without ``status``, append ``completed``."""
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    async def _original_without_status(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": ["media_buy"]}

    mod._ORIGINAL = _original_without_status
    try:
        result = await _get_adcp_capabilities_patched(object())
        assert result["status"] == "completed"
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_portfolio_publisher_domains_populated_sorted() -> None:
    """Portfolio.publisher_domains is sorted alphabetically per
    CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01.

    Covers: CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01
    """
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    async def _original(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": ["media_buy"]}

    mod._ORIGINAL = _original
    try:
        with patch(
            "core.platforms._capabilities_envelope._publisher_domains_for_current_tenant",
            return_value=["alpha.com", "mike.com", "zeta.com"],
        ):
            result = await _get_adcp_capabilities_patched(object())
        assert result["portfolio"]["publisher_domains"] == ["alpha.com", "mike.com", "zeta.com"]
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_portfolio_omitted_when_no_publisher_domains() -> None:
    """``Portfolio.publisher_domains`` has ``min_length=1`` in the AdCP
    schema, so omit the portfolio block entirely when the tenant has no
    publisher partners — emitting an empty list would fail spec validation.

    Covers: CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01
    """
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    async def _original(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": ["media_buy"]}

    mod._ORIGINAL = _original
    try:
        with patch(
            "core.platforms._capabilities_envelope._publisher_domains_for_current_tenant",
            return_value=[],
        ):
            result = await _get_adcp_capabilities_patched(object())
        assert "portfolio" not in result, "portfolio must be omitted when tenant has no publisher_domains"
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_portfolio_publisher_domains_merge_with_existing_portfolio() -> None:
    """If the SDK ever starts emitting a portfolio block, we merge into it
    rather than clobber. Forward-compat guard for the day the upstream
    capabilities response grows native portfolio support.
    """
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    async def _original_with_portfolio(self, params, context):  # noqa: ANN001
        return {
            "adcp": {},
            "supported_protocols": ["media_buy"],
            "portfolio": {"description": "test portfolio"},
        }

    mod._ORIGINAL = _original_with_portfolio
    try:
        with patch(
            "core.platforms._capabilities_envelope._publisher_domains_for_current_tenant",
            return_value=["alpha.com"],
        ):
            result = await _get_adcp_capabilities_patched(object())
        assert result["portfolio"]["description"] == "test portfolio"
        assert result["portfolio"]["publisher_domains"] == ["alpha.com"]
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_webhook_signing_capability_appended() -> None:
    """Webhook signing capability is populated from the tenant-specific helper."""
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    async def _original(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": ["media_buy"]}

    capability = {
        "supported": True,
        "profile": "adcp/webhook-signing/v1",
        "algorithms": ["ed25519"],
        "legacy_hmac_fallback": True,
    }

    mod._ORIGINAL = _original
    try:
        with patch(
            "core.platforms._capabilities_envelope._webhook_signing_for_current_tenant",
            return_value=capability,
        ):
            result = await _get_adcp_capabilities_patched(object())
        assert result["webhook_signing"] == capability
    finally:
        mod._ORIGINAL = _ORIGINAL


def test_webhook_signing_unsupported_without_current_tenant() -> None:
    """Discovery stays valid even when no tenant context is present."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant

    with patch("core.platforms._capabilities_envelope.current_tenant", return_value=None):
        assert _webhook_signing_for_current_tenant() == {"supported": False, "legacy_hmac_fallback": True}


def test_webhook_signing_supported_for_active_local_credential() -> None:
    """A usable local signing credential advertises the AdCP signing profile."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant

    with (
        patch("core.platforms._capabilities_envelope.current_tenant", return_value=SimpleNamespace(id="tenant_1")),
        patch(
            "src.services.webhook_signing.load_active_signing_credential", return_value=SimpleNamespace(alg="ed25519")
        ) as load_mock,
    ):
        assert _webhook_signing_for_current_tenant() == {
            "supported": True,
            "profile": "adcp/webhook-signing/v1",
            "algorithms": ["ed25519"],
            "legacy_hmac_fallback": True,
        }
    load_mock.assert_called_once_with(tenant_id="tenant_1", signing_mode="rfc9421")


def test_webhook_signing_unsupported_when_credential_load_fails() -> None:
    """Missing rows, KMS backends, unreadable PEMs, and bad JWKs stay unsupported."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant
    from src.services.webhook_signing import SigningConfigurationError

    with (
        patch("core.platforms._capabilities_envelope.current_tenant", return_value=SimpleNamespace(id="tenant_1")),
        patch(
            "src.services.webhook_signing.load_active_signing_credential",
            side_effect=SigningConfigurationError("failed to read PEM"),
        ) as load_mock,
    ):
        assert _webhook_signing_for_current_tenant() == {"supported": False, "legacy_hmac_fallback": True}
    load_mock.assert_called_once_with(tenant_id="tenant_1", signing_mode="rfc9421")
