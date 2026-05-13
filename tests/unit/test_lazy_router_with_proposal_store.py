"""Pin the :class:`_LazyPlatformRouterWithStore` wiring.

The framework's :mod:`adcp.decisioning.proposal_dispatch` duck-types
``hasattr(platform, "proposal_store_for_tenant")`` to find the wired
store. The upstream :class:`LazyPlatformRouter` doesn't expose this
accessor (only the eager :class:`PlatformRouter` does, via its
``proposal_stores=`` kwarg). Our subclass adds it so the lazy router
can be plugged into the proposal lifecycle.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from adcp.decisioning import (
    DecisioningCapabilities,
    DecisioningPlatform,
)
from adcp.decisioning.capabilities import Adcp, IdempotencySupported

from core.main import _LazyPlatformRouterWithStore
from src.core.database.repositories import SalesAgentProposalStore


def _bare_capabilities() -> DecisioningCapabilities:
    """Minimal capabilities envelope — enough for router construction."""
    return DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=3600),
        ),
    )


def _stub_factory(_tenant_id: str) -> DecisioningPlatform:
    """Factory stub — never called in these tests (no real dispatch)."""
    raise AssertionError("factory should not be called for store-wiring tests")


def test_router_exposes_proposal_store_for_tenant():
    """The accessor the framework's ``proposal_dispatch`` duck-types
    must exist on the router and return the wired store."""
    store = SalesAgentProposalStore()
    router = _LazyPlatformRouterWithStore(
        accounts=MagicMock(),
        factory=_stub_factory,
        capabilities=_bare_capabilities(),
        proposal_store=store,
    )

    assert hasattr(router, "proposal_store_for_tenant"), (
        "proposal_dispatch reads via hasattr(); the accessor must be present"
    )
    assert router.proposal_store_for_tenant("any_tenant") is store, (
        "Single shared store across tenants — tenant isolation runs inside "
        "the store on expected_account_id, not by handing each tenant a "
        "different instance"
    )


def test_router_returns_none_when_no_store_wired():
    """Constructing the router without a store leaves
    ``proposal_store_for_tenant`` returning ``None`` so the framework's
    duck-typed dispatch falls back to the v1 (no-proposal) path."""
    router = _LazyPlatformRouterWithStore(
        accounts=MagicMock(),
        factory=_stub_factory,
        capabilities=_bare_capabilities(),
        # proposal_store omitted
    )
    assert router.proposal_store_for_tenant("any_tenant") is None


def test_router_is_lazy_platform_router_subclass():
    """The subclass must remain instance-compatible with
    :class:`LazyPlatformRouter` — the SDK's ``serve()`` dispatcher
    runs :func:`isinstance` checks and the wider salesagent code
    treats ``build_router()`` returns as ``LazyPlatformRouter``."""
    from adcp.decisioning import LazyPlatformRouter

    router = _LazyPlatformRouterWithStore(
        accounts=MagicMock(),
        factory=_stub_factory,
        capabilities=_bare_capabilities(),
    )
    assert isinstance(router, LazyPlatformRouter)
