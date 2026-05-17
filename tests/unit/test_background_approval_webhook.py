"""Verify _send_approval_webhook emits media_buy.status_changed via the
fire-and-forget subscriber pattern, mapping the workflow-step outcome
(``completed`` / ``failed``) to buyer-facing media-buy state
(``approved`` / ``rejected``).

The function runs in the GAM manual-approval polling thread — emission
must never raise back to the caller, and missing media_buy rows must
log + skip rather than fire a misleading event.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.services.background_approval_service import _send_approval_webhook


def _mock_uow_with_media_buy(media_buy):
    """Return a MagicMock that mimics `MediaBuyUoW(tenant_id) as uow:` and
    yields a uow whose `.media_buys.get_by_id(order_id)` returns *media_buy*.
    """
    uow = MagicMock()
    uow.media_buys.get_by_id.return_value = media_buy
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = False
    return cm


class TestSendApprovalWebhook:
    def test_emits_approved_on_completed_status(self):
        media_buy = MagicMock(media_buy_id="mb_42", buyer_ref="po_99")

        with (
            patch(
                "src.services.background_approval_service.MediaBuyUoW",
                return_value=_mock_uow_with_media_buy(media_buy),
            ),
            patch("src.admin.services.webhook_publisher.emit_event") as mock_emit,
        ):
            _send_approval_webhook(
                tenant_id="t1",
                order_id="gam_order_999",
                workflow_step_id="wf_abc",
                status="completed",
            )

        mock_emit.assert_called_once_with(
            "t1",
            "media_buy.status_changed",
            {
                "media_buy_id": "mb_42",
                "buyer_ref": "po_99",
                "status": "approved",
                "workflow_step_id": "wf_abc",
            },
        )

    def test_emits_rejected_on_failed_status(self):
        media_buy = MagicMock(media_buy_id="mb_42", buyer_ref="po_99")

        with (
            patch(
                "src.services.background_approval_service.MediaBuyUoW",
                return_value=_mock_uow_with_media_buy(media_buy),
            ),
            patch("src.admin.services.webhook_publisher.emit_event") as mock_emit,
        ):
            _send_approval_webhook(
                tenant_id="t1",
                order_id="gam_order_999",
                workflow_step_id="wf_abc",
                status="failed",
            )

        mock_emit.assert_called_once_with(
            "t1",
            "media_buy.status_changed",
            {
                "media_buy_id": "mb_42",
                "buyer_ref": "po_99",
                "status": "rejected",
                "workflow_step_id": "wf_abc",
            },
        )

    def test_does_not_emit_when_media_buy_not_found(self):
        """If the order_id doesn't resolve to a MediaBuy row, skip the
        emit — firing a status_changed event with no media_buy_id would
        mislead subscribers."""
        with (
            patch(
                "src.services.background_approval_service.MediaBuyUoW",
                return_value=_mock_uow_with_media_buy(None),
            ),
            patch("src.admin.services.webhook_publisher.emit_event") as mock_emit,
        ):
            _send_approval_webhook(
                tenant_id="t1",
                order_id="missing_order",
                workflow_step_id="wf_abc",
                status="completed",
            )

        mock_emit.assert_not_called()

    def test_does_not_raise_when_uow_fails(self):
        """DB failure during the lookup must not propagate to the caller —
        the polling thread can't roll back the actual approval."""
        broken_cm = MagicMock()
        broken_cm.__enter__.side_effect = RuntimeError("db unavailable")
        broken_cm.__exit__.return_value = False

        with (
            patch("src.services.background_approval_service.MediaBuyUoW", return_value=broken_cm),
            patch("src.admin.services.webhook_publisher.emit_event") as mock_emit,
        ):
            # No exception expected.
            _send_approval_webhook(
                tenant_id="t1",
                order_id="gam_order_999",
                workflow_step_id="wf_abc",
                status="completed",
            )

        mock_emit.assert_not_called()
