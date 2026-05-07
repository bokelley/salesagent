"""Regression test: ``MockSellerPlatform.sync_creatives`` must persist
to the database via the shared delegate.

Issue #88: the e2e ``test_creative_sync_with_assignment_in_single_call``
fails at "Creative <id> should be in list" — sync claims
action=created, but the subsequent list_creatives call doesn't return
it. Trace: the mock's ``sync_creatives`` was a stub that fabricated
``action="created"`` without writing to the DB; ``list_creatives``
delegates properly to the impl which reads from the DB → finds
nothing.

Same defect class as the ``get_media_buy_delivery`` mock stub fixed in
PR #84 / issue #54: a placeholder method shipping success-shaped wire
output without performing the actual side-effect. Sync and list use
the same DB scope, so the integration test in PR #96 passes (impl-level
flow is correct); the e2e symptom comes from the mock's stub.

Fix: make the mock dispatch to ``_delegate_sync_creatives`` so it
goes through the same impl GamPlatform uses (real DB write).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest


def test_mock_sync_creatives_is_async() -> None:
    """Method must be async because the delegate it dispatches to is async."""
    from core.platforms.mock import MockSellerPlatform

    assert inspect.iscoroutinefunction(MockSellerPlatform.sync_creatives)


@pytest.mark.asyncio
async def test_mock_sync_creatives_actually_invokes_shared_delegate() -> None:
    """Behavioral test: invoking the method awaits
    ``_delegate_sync_creatives`` with the buyer-supplied req and ctx.

    Catches a regression where the method goes back to a stub that
    fabricates ``action="created"`` without persisting — same defect
    class that broke the e2e creative-sync flow in #88.
    """
    from core.platforms.mock import MockSellerPlatform

    platform = MockSellerPlatform()
    fake_req = object()
    fake_ctx = object()

    expected = {"creatives": [{"creative_id": "c1", "action": "created", "status": "approved"}]}
    delegate_mock = AsyncMock(return_value=expected)

    with patch("core.platforms.mock._delegate_sync_creatives", delegate_mock):
        # Bypass @_IDEMPOTENCY.wrap to avoid the cache-key requirement; the
        # contract under test is "method delegates", not "cache replay
        # behaviour". Idempotency is exercised by the integration tier.
        underlying = MockSellerPlatform.sync_creatives.__wrapped__  # type: ignore[attr-defined]
        result = await underlying(platform, fake_req, fake_ctx)

    delegate_mock.assert_awaited_once_with(fake_req, fake_ctx)
    assert result == expected
