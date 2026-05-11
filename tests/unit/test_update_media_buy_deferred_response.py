"""Tests for the deferred-update response shape on update_media_buy.

Covers tescoboy issue #158: pre-fix, when an `update_media_buy` call
landed in a `requires_approval` workflow step the MCP response was
`{media_buy_id, affected_packages: []}` — byte-identical to a
successful no-op. The buyer's program could not disambiguate "queued
for approval" from "applied with zero package effect".

The fix surfaces `workflow_step_id` on the wire so async buyer
programs can poll for the final outcome, and updates the model's
`__str__` so the protocol envelope's `message` field carries a
deferred-aware human-readable signal.
"""

from src.core.schemas import UpdateMediaBuySuccess
from tests.factories.spec_required_kwargs import required_request_kwargs


class TestDeferredResponseDistinguishableFromNoOp:
    """Wire shapes diverge between deferred and no-op apply."""

    def test_deferred_wire_carries_workflow_step_id(self):
        deferred = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            affected_packages=[],
            workflow_step_id="step_pending_001",
        )
        wire = deferred.model_dump(mode="json", exclude_none=True)

        assert wire["media_buy_id"] == "mb_1"
        assert wire["affected_packages"] == []
        assert wire["workflow_step_id"] == "step_pending_001"

    def test_immediate_apply_wire_omits_workflow_step_id(self):
        applied = UpdateMediaBuySuccess(media_buy_id="mb_2", affected_packages=[])
        wire = applied.model_dump(mode="json", exclude_none=True)

        # exclude_none drops the unset workflow_step_id — keeping the wire
        # tidy on the immediate-apply path so the new field doesn't leak.
        assert "workflow_step_id" not in wire
        assert wire["media_buy_id"] == "mb_2"

    def test_deferred_and_noop_wires_are_not_equal(self):
        # The exact bug from #158: pre-fix, these two payloads were
        # byte-identical. After the fix the deferred response carries
        # workflow_step_id while the no-op apply does not.
        deferred = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            affected_packages=[],
            workflow_step_id="step_pending_001",
        ).model_dump(mode="json", exclude_none=True)
        no_op = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            affected_packages=[],
        ).model_dump(mode="json", exclude_none=True)

        assert deferred != no_op


class TestEnvelopeMessageReflectsDeferredState:
    """`__str__` is consumed by ProtocolEnvelope.wrap as the `message` field."""

    def test_deferred_message_mentions_approval_and_step_id(self):
        deferred = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            affected_packages=[],
            workflow_step_id="step_pending_001",
        )
        msg = str(deferred)
        assert "approval" in msg.lower()
        assert "step_pending_001" in msg
        assert "Poll" in msg

    def test_immediate_apply_message_has_no_approval_language(self):
        applied = UpdateMediaBuySuccess(media_buy_id="mb_2", affected_packages=[])
        msg = str(applied)
        assert "approval" not in msg.lower()
        assert "queued" not in msg.lower()

    def test_message_with_affected_packages_uses_count(self):
        from src.core.schemas import AffectedPackage

        applied = UpdateMediaBuySuccess(
            media_buy_id="mb_3",
            affected_packages=[
                AffectedPackage(package_id="pkg_a", paused=False),
                AffectedPackage(package_id="pkg_b", paused=False),
            ],
        )
        assert "2 package" in str(applied)


class TestDeferredResponseConstructedByImpl:
    """The impl populates workflow_step_id when it lands in requires_approval."""

    def test_impl_deferred_path_sets_workflow_step_id(self):
        # Behavioral test: spy on UpdateMediaBuySuccess construction inside
        # the impl by looking at what update_workflow_step records as
        # response_data — the impl serializes the response there.
        from datetime import UTC, datetime
        from unittest.mock import MagicMock, patch

        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_update import _update_media_buy_impl

        identity = ResolvedIdentity(
            principal_id="p_1",
            tenant_id="t_1",
            tenant={"tenant_id": "t_1", "name": "Test"},
            testing_context=AdCPTestContext(dry_run=False),
        )

        # Mock UoW + session; impl creates a workflow step then short-circuits
        # via the manual_approval_required gate.
        mock_uow = MagicMock()
        mock_uow.session = MagicMock()
        mock_uow.media_buys = MagicMock()
        mock_uow.media_buys.find_by_idempotency_key.return_value = None
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.media_buys.get_by_id.return_value = MagicMock(
            media_buy_id="mb_1",
            principal_id="p_1",
            external_id=None,
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 12, 31, tzinfo=UTC),
            currency="USD",
        )
        mock_uow.media_buys.get_packages.return_value = []  # no guaranteed packages

        recorded = {}

        def record_step(step_id, **kwargs):
            recorded.setdefault(step_id, []).append(kwargs)
            return MagicMock()

        mock_step = MagicMock(step_id="step_pending_001", context_id="ctx_001")
        mock_ctx_manager = MagicMock()
        mock_ctx_manager.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        mock_ctx_manager.create_workflow_step.return_value = mock_step
        mock_ctx_manager.update_workflow_step.side_effect = record_step

        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = True
        mock_adapter.manual_approval_operations = ["update_media_buy"]

        MODULE = "src.core.tools.media_buy_update"
        with (
            patch(f"{MODULE}.MediaBuyUoW", return_value=mock_uow),
            patch(f"{MODULE}.get_principal_object", return_value=MagicMock(principal_id="p_1")),
            patch(f"{MODULE}._verify_principal"),
            patch(f"{MODULE}.get_context_manager", return_value=mock_ctx_manager),
            patch(f"{MODULE}.get_adapter", return_value=mock_adapter),
            patch(f"{MODULE}.is_projected_media_buy_id", return_value=False),
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value={"tenant_id": "t_1"}),
            patch("src.core.audit_logger.AuditLogger"),
        ):
            req = UpdateMediaBuyRequest(
                **required_request_kwargs(), media_buy_id="mb_1", end_time="2026-06-01T00:00:00Z"
            )
            result = _update_media_buy_impl(req=req, identity=identity)

        assert result.workflow_step_id == "step_pending_001"
        assert result.affected_packages == []
        # And the wire response carries it
        wire = result.model_dump(mode="json", exclude_none=True)
        assert wire["workflow_step_id"] == "step_pending_001"
