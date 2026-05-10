"""Wire-code coverage for re-cancel of an already-canceled buy (issue #317).

The AdCP storyboard step ``media_buy_seller/invalid_transitions/second_cancel``
asserts that re-canceling an already-canceled buy surfaces the spec code
``NOT_CANCELLABLE`` with ``recovery="correctable"``.

These tests pin both layers (mirroring PR #128's pattern):

1. ``_update_media_buy_impl`` raises ``AdCPNotCancellableError``
   (transport-agnostic, BEFORE adapter dispatch — idempotency-spec friendly).
2. ``_delegate_update_media_buy`` translates that into the framework's
   wire-shaped ``AdcpError`` so the dispatcher emits ``NOT_CANCELLABLE``
   with ``recovery="correctable"``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import AdCPNotCancellableError
from src.core.schemas import UpdateMediaBuyRequest
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.unit._update_media_buy_helpers import (
    UpdateMediaBuyImplFixture,
    make_delegate_ctx,
    make_identity,
    run_delegate_coro,
)

# ---------------------------------------------------------------------------
# Layer 1: _impl raises AdCPNotCancellableError on re-cancel of canceled buy
# ---------------------------------------------------------------------------


class TestImplRaisesNotCancellableOnReCancel:
    """``_update_media_buy_impl`` raises AdCPNotCancellableError when buyer
    attempts to cancel a buy whose status is already ``canceled``."""

    def test_re_cancel_of_canceled_buy_raises_not_cancellable(self) -> None:
        """canceled=True against an already-canceled buy raises AdCPNotCancellableError."""
        existing_mb = MagicMock()
        existing_mb.status = "canceled"
        existing_mb.currency = "USD"

        with UpdateMediaBuyImplFixture(existing_media_buy=existing_mb):
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_canceled",
                canceled=True,
                cancellation_reason="Deliberate re-cancel to force NOT_CANCELLABLE",
            )
            with pytest.raises(AdCPNotCancellableError) as exc_info:
                _update_media_buy_impl(req=req, identity=make_identity())

        assert exc_info.value.error_code == "NOT_CANCELLABLE"
        assert exc_info.value.recovery == "correctable"
        assert exc_info.value.status_code == 422
        assert "mb_canceled" in str(exc_info.value)

    def test_error_class_attributes_match_spec(self) -> None:
        """AdCPNotCancellableError carries the spec-correct wire vocabulary."""
        exc = AdCPNotCancellableError("boom")
        assert exc.error_code == "NOT_CANCELLABLE"
        assert exc.recovery == "correctable"
        assert exc.status_code == 422


# ---------------------------------------------------------------------------
# Layer 2: delegate translates AdCPNotCancellableError -> wire AdcpError
# ---------------------------------------------------------------------------


class TestDelegateProjectsNotCancellableToWireEnvelope:
    """``_delegate_update_media_buy`` translates AdCPNotCancellableError into
    the framework's decisioning ``AdcpError`` so the dispatcher emits a
    spec-compliant ``adcp_error.code = NOT_CANCELLABLE`` envelope."""

    def test_not_cancellable_projects_to_wire_code(self) -> None:
        from adcp.decisioning.types import AdcpError

        from core.platforms._delegate import _delegate_update_media_buy

        ctx = make_delegate_ctx()

        with patch(
            "core.platforms._delegate._update_media_buy_impl",
            side_effect=AdCPNotCancellableError(
                "media_buy_id='mb_x' is already canceled — cannot cancel a terminal buy"
            ),
        ):
            with pytest.raises(AdcpError) as exc_info:
                run_delegate_coro(_delegate_update_media_buy("mb_x", {"canceled": True}, ctx))

        assert exc_info.value.code == "NOT_CANCELLABLE"
        assert exc_info.value.recovery == "correctable"
        assert "mb_x" in (exc_info.value.args[0] if exc_info.value.args else "")
