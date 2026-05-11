"""Tests for the update_media_buy replay path on workflow approval.

Covers tescoboy issue #143: ``approve_workflow_step`` previously only
replayed ``create_media_buy``. Approving a deferred ``update_media_buy``
step flipped status to ``approved`` and applied nothing — buyer believed
the change shipped, publisher believed they approved it, neither the DB
nor GAM reflected the request.

These tests target ``_replay_update_media_buy`` directly: it is the
helper that reconstructs identity from the step's ``Context`` row and
re-enters ``_update_media_buy_impl`` with ``bypass_manual_approval=True``.
"""

from unittest.mock import MagicMock, patch

from src.admin.blueprints.workflows import _replay_update_media_buy
from src.core.schemas import Error, UpdateMediaBuyError, UpdateMediaBuySuccess


def _make_step(request_data, context_id="ctx_1", step_id="step_1"):
    step = MagicMock()
    step.step_id = step_id
    step.context_id = context_id
    step.request_data = request_data
    return step


def _make_db_with_context(principal_id):
    """Build a DB session whose `scalars().first()` returns a Context."""
    context = MagicMock()
    context.context_id = "ctx_1"
    context.principal_id = principal_id

    scalars = MagicMock()
    scalars.first.return_value = context if principal_id else None

    db = MagicMock()
    db.scalars.return_value = scalars
    return db


class TestReplayUpdateMediaBuySuccess:
    def test_success_calls_impl_with_reconstructed_identity(self):
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
                "end_time": "2026-06-01T00:00:00Z",
            }
        )
        db = _make_db_with_context(principal_id="p_1")
        success_response = UpdateMediaBuySuccess(media_buy_id="mb_1", affected_packages=[])

        with (
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t_1"}),
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                return_value=success_response,
            ) as mock_impl,
        ):
            success, err_msg = _replay_update_media_buy(step, "t_1", db)

        assert success is True
        assert err_msg is None
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args.kwargs
        assert call_kwargs["bypass_manual_approval"] is True
        assert call_kwargs["context_id"] == "ctx_1"
        identity = call_kwargs["identity"]
        assert identity.principal_id == "p_1"
        assert identity.tenant_id == "t_1"


class TestReplayUpdateMediaBuyFailure:
    def test_impl_returns_error_surfaces_message(self):
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
            }
        )
        db = _make_db_with_context(principal_id="p_1")
        error_response = UpdateMediaBuyError(errors=[Error(code="adapter_error", message="GAM rejected dates")])

        with (
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t_1"}),
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                return_value=error_response,
            ),
        ):
            success, err_msg = _replay_update_media_buy(step, "t_1", db)

        assert success is False
        assert err_msg == "GAM rejected dates"

    def test_impl_raises_surfaces_message(self):
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
            }
        )
        db = _make_db_with_context(principal_id="p_1")

        with (
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t_1"}),
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                side_effect=RuntimeError("connection lost"),
            ),
        ):
            success, err_msg = _replay_update_media_buy(step, "t_1", db)

        assert success is False
        assert "connection lost" in err_msg

    def test_missing_principal_returns_error_without_calling_impl(self):
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
            }
        )
        db = _make_db_with_context(principal_id=None)

        with patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl:
            success, err_msg = _replay_update_media_buy(step, "t_1", db)

        assert success is False
        assert "principal" in err_msg
        mock_impl.assert_not_called()

    def test_missing_tenant_returns_error_without_calling_impl(self):
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
            }
        )
        db = _make_db_with_context(principal_id="p_1")

        with (
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl,
        ):
            success, err_msg = _replay_update_media_buy(step, "t_1", db)

        assert success is False
        assert "Tenant t_1 not found" in err_msg
        mock_impl.assert_not_called()

    def test_unparseable_request_data_returns_error(self):
        # `start_time` cannot be cast to an aware datetime — the schema
        # rejects on validate. Replay surfaces the validation error
        # instead of letting it crash the approve handler.
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
                "start_time": "not-a-date",
            }
        )
        db = _make_db_with_context(principal_id="p_1")

        with (
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t_1"}),
            patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl,
        ):
            success, err_msg = _replay_update_media_buy(step, "t_1", db)

        assert success is False
        assert "reconstruct" in err_msg
        mock_impl.assert_not_called()


class TestReplayUpdateMediaBuyPayloadFiltering:
    def test_protocol_metadata_field_stripped_before_validation(self):
        # The impl writes `request_metadata={"protocol": identity.protocol}`
        # alongside the request payload when persisting the step. That key
        # is not a field on UpdateMediaBuyRequest. Without filtering, strict
        # extra=forbid validation would reject the replay.
        step = _make_step(
            {
                "account": {"account_id": "test-acct"},
                "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
                "media_buy_id": "mb_1",
                "protocol": "mcp",
            }
        )
        db = _make_db_with_context(principal_id="p_1")
        success_response = UpdateMediaBuySuccess(media_buy_id="mb_1", affected_packages=[])

        with (
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t_1"}),
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                return_value=success_response,
            ) as mock_impl,
        ):
            success, _ = _replay_update_media_buy(step, "t_1", db)

        assert success is True
        # The request actually validated and reached the impl — protocol
        # was filtered out before model_validate ran.
        req = mock_impl.call_args.kwargs["req"]
        assert req.media_buy_id == "mb_1"


class TestBypassManualApprovalParameter:
    def test_impl_signature_accepts_bypass(self):
        import inspect

        from src.core.tools.media_buy_update import _update_media_buy_impl

        sig = inspect.signature(_update_media_buy_impl)
        assert "bypass_manual_approval" in sig.parameters
        assert sig.parameters["bypass_manual_approval"].default is False
