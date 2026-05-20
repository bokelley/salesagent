"""``get_adcp_capabilities`` response patches.

Two amendments on the SDK's default capabilities response (see
``core.platforms._capabilities_envelope``):

1. Envelope ``status`` field — AdCP 3.0.11 protocol-envelope schema
   requires it. The upstream SDK doesn't emit it yet.
2. ``portfolio.publisher_domains`` — AdCP v3 moved publisher portfolio
   from the retired ``list_authorized_properties`` onto this response.
   Salesagent populates it per-tenant from ``PublisherPartner``.

This test pins both shims — if a future SDK revision adds them natively,
the assertions still pass against the SDK's output and we can drop the
workarounds (and these pin tests).
"""

from __future__ import annotations

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
