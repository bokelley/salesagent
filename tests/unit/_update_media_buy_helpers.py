"""Shared fixtures for ``_update_media_buy_impl`` and delegate wire-translation tests.

Both ``test_update_media_buy_not_found_codes.py`` (issue #73) and
``test_update_media_buy_not_cancellable.py`` (issue #317) drive the same
two layers (impl + delegate) with the same mock topology. Extracted here
to satisfy the DRY invariant.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext

# The module path that the ``_update_media_buy_impl`` fixture patches against.
# Matches the import path of the impl under test.
IMPL_MODULE = "src.core.tools.media_buy_update"


def make_identity(
    principal_id: str = "principal_a",
    tenant_id: str = "tenant_a",
) -> ResolvedIdentity:
    """Build a minimal ResolvedIdentity that satisfies the impl entrypoint."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test"},
        protocol="mcp",
        testing_context=AdCPTestContext(),
    )


class UpdateMediaBuyImplFixture:
    """Context manager wiring up just enough mocks for ``_update_media_buy_impl``
    to reach the validation/dispatch branches without touching real DB code paths.

    Yields the UoW mock instance so tests can configure repository returns
    (``uow.media_buys.get_by_id``, ``uow.media_buys.get_package``, etc.).

    Args:
        manual_approval: When True, the mocked adapter declares update_media_buy
            requires manual approval. Lets tests exercise the manual-approval
            short-circuit path.
        existing_media_buy: Optional MagicMock representing the current media
            buy state. When provided, ``uow.media_buys.get_by_id`` returns it
            (drives e.g. status="canceled" pre-validation in the cancel branch).
    """

    def __init__(
        self,
        *,
        manual_approval: bool = False,
        existing_media_buy: Any | None = None,
    ) -> None:
        self._manual_approval = manual_approval
        self._existing_media_buy = existing_media_buy

    def __enter__(self) -> Any:
        self._patchers: list[Any] = []

        ctx_mgr = MagicMock()
        ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_001")

        mock_session = MagicMock()
        uow = MagicMock()
        uow.session = mock_session
        uow.media_buys = MagicMock()
        if self._existing_media_buy is not None:
            uow.media_buys.get_by_id.return_value = self._existing_media_buy
        uow.currency_limits = MagicMock()
        cl = MagicMock()
        cl.max_daily_package_spend = None
        cl.min_package_budget = 0
        uow.currency_limits.get_for_currency.return_value = cl
        uow.__enter__ = Mock(return_value=uow)
        uow.__exit__ = Mock(return_value=False)

        adapter = MagicMock()
        adapter.manual_approval_required = self._manual_approval
        adapter.manual_approval_operations = ["update_media_buy"] if self._manual_approval else []

        targets = {
            f"{IMPL_MODULE}._verify_principal": MagicMock(return_value=None),
            f"{IMPL_MODULE}.get_principal_object": MagicMock(
                return_value=MagicMock(principal_id="principal_a", name="P", platform_mappings={})
            ),
            f"{IMPL_MODULE}.get_context_manager": MagicMock(return_value=ctx_mgr),
            f"{IMPL_MODULE}.MediaBuyUoW": MagicMock(return_value=uow),
            f"{IMPL_MODULE}.get_adapter": MagicMock(return_value=adapter),
            f"{IMPL_MODULE}.get_audit_logger": MagicMock(return_value=MagicMock()),
        }
        for target, value in targets.items():
            p = patch(target, value)
            p.start()
            self._patchers.append(p)
        return uow

    def __exit__(self, *exc: object) -> bool:
        for p in reversed(self._patchers):
            p.stop()
        return False


def make_delegate_ctx() -> Any:
    """Minimal ctx that satisfies the delegate's ``_build_identity()`` call."""
    ctx = MagicMock()
    ctx.account.metadata.get.return_value = "tenant_a"
    return ctx


def run_delegate_coro(coro: Any) -> Any:
    """Run a delegate coroutine with the auth/tenant lookups mocked.

    Both test surfaces drive the delegate the same way: tenant_id resolution
    via ``get_tenant_by_id`` and principal_id via the ``current_principal``
    contextvar. Patch them centrally here.
    """
    with patch(
        "core.platforms._delegate.get_tenant_by_id",
        return_value={"tenant_id": "tenant_a", "name": "Test"},
    ):
        with patch(
            "core.platforms._delegate.current_principal",
            MagicMock(get=MagicMock(return_value="principal_a")),
        ):
            return asyncio.run(coro)
