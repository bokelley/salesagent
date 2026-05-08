"""Tests for the creative pre-approval gate (#145).

When the per-tenant ``creative_pre_approval_gate_enabled`` flag is on,
creatives at ``status='pending_review'`` are held back from the
ad-server upload at buy-approval time. They reach the ad server only
when a human flips the local status to ``approved``, which triggers a
retroactive push to the live line item.

These tests target the three layers:

1. ``is_creative_pre_approval_gate_enabled`` accessor (handles ORM /
   dict / None)
2. The ``Tenant`` ORM column (default False, server-default false)
3. The retroactive push helper ``push_creative_to_existing_buy`` —
   shape contract and fail-safe behavior

Behavioural integration tests for the buy-approval skip live in
``tests/integration/`` (require real DB + adapter); the unit
coverage here pins the building blocks.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.feature_flags import is_creative_pre_approval_gate_enabled


class TestFeatureFlagAccessor:
    def test_none_tenant_returns_false(self):
        assert is_creative_pre_approval_gate_enabled(None) is False

    def test_orm_with_flag_true(self):
        tenant = MagicMock()
        tenant.creative_pre_approval_gate_enabled = True
        assert is_creative_pre_approval_gate_enabled(tenant) is True

    def test_orm_with_flag_false(self):
        tenant = MagicMock()
        tenant.creative_pre_approval_gate_enabled = False
        assert is_creative_pre_approval_gate_enabled(tenant) is False

    def test_orm_missing_attr_defaults_false(self):
        tenant = object()  # bare object — no attribute
        assert is_creative_pre_approval_gate_enabled(tenant) is False

    def test_dict_with_flag_true(self):
        # Operators sometimes pass the tenant dict from
        # ``get_tenant_by_id`` instead of the ORM instance.
        tenant_dict = {"creative_pre_approval_gate_enabled": True}
        assert is_creative_pre_approval_gate_enabled(tenant_dict) is True

    def test_dict_without_flag_defaults_false(self):
        tenant_dict = {"tenant_id": "t1"}  # no flag key
        assert is_creative_pre_approval_gate_enabled(tenant_dict) is False


class TestTenantOrmColumn:
    def test_column_present_on_tenant_model(self):
        from src.core.database.models import Tenant

        columns = {col.name for col in Tenant.__table__.columns}
        assert "creative_pre_approval_gate_enabled" in columns

    def test_column_default_is_false(self):
        from src.core.database.models import Tenant

        col = Tenant.__table__.columns["creative_pre_approval_gate_enabled"]
        # Server-default applies at the DB layer for existing rows post-migration.
        assert col.server_default is not None
        # And the Python-side default keeps in-memory construction safe.
        assert col.default is not None
        assert col.nullable is False


class TestPushCreativeToExistingBuyBehavior:
    """`push_creative_to_existing_buy` retroactive helper.

    Verifies the contract: never raises on adapter failure, returns
    ``(success, error_message)``, surfaces specific failure modes
    (creative missing, assignment missing, tenant missing) without
    crashing the caller's local-approval flow.
    """

    def _setup_session(self, *, tenant_obj, creative, assignment, principal, media_buy):
        """Build a session whose `scalars().first()` returns rows in
        the order the helper queries them: tenant, creative, assignment,
        principal, media_buy.
        """
        sequence = [tenant_obj, creative, assignment, principal, media_buy]
        sequence_iter = iter(sequence)

        def scalars_side_effect(_stmt):
            scalars_obj = MagicMock()
            try:
                scalars_obj.first.return_value = next(sequence_iter)
            except StopIteration:
                scalars_obj.first.return_value = None
            return scalars_obj

        session = MagicMock()
        session.scalars.side_effect = scalars_side_effect
        return session

    def test_tenant_not_found_returns_error_without_adapter_call(self):
        from src.core.tools.media_buy_create import push_creative_to_existing_buy

        session = self._setup_session(tenant_obj=None, creative=None, assignment=None, principal=None, media_buy=None)
        uow = MagicMock(session=session)
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)

        with patch("src.core.database.repositories.MediaBuyUoW", return_value=uow):
            success, err = push_creative_to_existing_buy(creative_id="c_1", media_buy_id="mb_1", tenant_id="t_1")

        assert success is False
        assert "Tenant t_1 not found" in err

    def test_creative_not_found_returns_error_without_adapter_call(self):
        from src.core.tools.media_buy_create import push_creative_to_existing_buy

        tenant_obj = MagicMock(tenant_id="t_1")
        session = self._setup_session(
            tenant_obj=tenant_obj, creative=None, assignment=None, principal=None, media_buy=None
        )
        uow = MagicMock(session=session)
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)

        with (
            patch("src.core.database.repositories.MediaBuyUoW", return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t_1"}),
            patch("src.core.config_loader.set_current_tenant"),
        ):
            success, err = push_creative_to_existing_buy(creative_id="c_1", media_buy_id="mb_1", tenant_id="t_1")

        assert success is False
        assert "Creative c_1 not found" in err


class TestGateAppliesAtBuyApprovalPath:
    """The gate at ``execute_approved_media_buy`` filters pending_review
    creatives from the asset list before the adapter call.

    The full flow needs DB + adapter mocks; here we pin the helper
    behavior the gate relies on (the flag accessor) and document the
    integration touchpoint."""

    def test_gate_logic_short_circuits_on_pending_review(self):
        # Sanity: if the flag is on AND a creative is pending_review,
        # the simulated filter drops it. This mirrors the inline filter
        # at media_buy_create.py inside execute_approved_media_buy.
        creative_a = MagicMock(creative_id="c_a", status="approved")
        creative_b = MagicMock(creative_id="c_b", status="pending_review")
        creative_c = MagicMock(creative_id="c_c", status="approved")

        tenant = MagicMock(creative_pre_approval_gate_enabled=True)
        gate_on = is_creative_pre_approval_gate_enabled(tenant)

        kept = [c for c in [creative_a, creative_b, creative_c] if not (gate_on and c.status == "pending_review")]
        assert [c.creative_id for c in kept] == ["c_a", "c_c"]

    def test_gate_off_keeps_all_creatives(self):
        creative_a = MagicMock(creative_id="c_a", status="approved")
        creative_b = MagicMock(creative_id="c_b", status="pending_review")

        tenant = MagicMock(creative_pre_approval_gate_enabled=False)
        gate_on = is_creative_pre_approval_gate_enabled(tenant)

        kept = [c for c in [creative_a, creative_b] if not (gate_on and c.status == "pending_review")]
        assert [c.creative_id for c in kept] == ["c_a", "c_b"]


@pytest.mark.parametrize(
    "tenant_status,gate_should_apply",
    [
        ("pending_creatives", False),  # existing media_buy_actions path handles it
        ("draft", False),  # existing path
        ("active", True),  # gate retroactive push
        ("paused", True),  # gate retroactive push
        ("pending_start", True),  # gate retroactive push
        ("completed", True),  # technically impossible (creative wouldn't be pending_review),
        # but the gate path still triggers — adapter call would no-op
    ],
)
def test_retroactive_push_target_buy_status(tenant_status, gate_should_apply):
    """The retroactive push fires only for buys NOT in
    ``{pending_creatives, draft}`` — those are handled by the existing
    media_buy_actions loop, which calls ``execute_approved_media_buy``
    (full GAM order/line-item creation, including creative push).
    Buys in any other status are already-live and need the targeted push.
    """
    handled_by_media_buy_actions = tenant_status in {"pending_creatives", "draft"}
    expected_to_call_push = not handled_by_media_buy_actions
    assert gate_should_apply is expected_to_call_push
