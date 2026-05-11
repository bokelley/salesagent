"""Capabilities envelope carries the canonical v3 ``status`` field.

Issue #349.2: the AdCP 3.0.11 ``protocol-envelope.json`` schema requires
a top-level ``status`` field on every response. The upstream SDK's
``capabilities_response()`` helper emits the body without it, so we patch
``PlatformHandler.get_adcp_capabilities`` to append ``status="completed"``.

This test pins the shim — if a future SDK revision adds the field natively
the patch becomes redundant and this test will start passing without the
shim, signalling we can drop the workaround.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_shim_is_installed_on_platform_handler() -> None:
    """Importing the shim module installs the patch on
    ``PlatformHandler.get_adcp_capabilities`` — confirms the side-effect
    import in ``core/main.py`` is functional and won't silently drop.
    """
    # Side-effect import installs the patch.
    from adcp.decisioning.handler import PlatformHandler

    from core.platforms import _capabilities_envelope  # noqa: F401

    assert (
        PlatformHandler.get_adcp_capabilities is _capabilities_envelope._get_adcp_capabilities_with_envelope_status
    ), "shim not installed — get_adcp_capabilities responses will be missing /status"


@pytest.mark.asyncio
async def test_status_appended_only_when_missing() -> None:
    """When the handler already emits ``status``, we don't clobber it."""

    # Patched method's source: call the original via the module-level cache.
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_with_envelope_status,
    )

    # Stub the original to return a body that already has status.
    async def _original_with_status(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": [], "status": "working"}

    # Temporarily replace _ORIGINAL to verify the merge logic.
    import core.platforms._capabilities_envelope as mod

    mod._ORIGINAL = _original_with_status
    try:
        result = await _get_adcp_capabilities_with_envelope_status(object())
        assert result["status"] == "working", "must not clobber existing status"
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_status_completed_appended_when_absent() -> None:
    """When the handler emits a body without ``status``, append ``completed``."""
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_with_envelope_status,
    )

    async def _original_without_status(self, params, context):  # noqa: ANN001
        return {"adcp": {}, "supported_protocols": ["media_buy"]}

    mod._ORIGINAL = _original_without_status
    try:
        result = await _get_adcp_capabilities_with_envelope_status(object())
        assert result["status"] == "completed"
    finally:
        mod._ORIGINAL = _ORIGINAL
