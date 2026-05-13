"""Pin :class:`SalesAgentProposalStore` class-level attributes against
silent regressions.

These tests are pure attribute checks — no DB needed. The full
behavioral suite lives in
``tests/integration/test_proposal_store.py`` (real Postgres).
"""

from __future__ import annotations

from datetime import timedelta

from src.core.database.repositories import SalesAgentProposalStore


def test_durable_flag_is_true():
    """``InMemoryProposalStore.is_durable == False`` triggers a
    production-mode warning from the framework. Our Postgres-backed
    store must declare ``True`` so the warning doesn't fire on
    production deploys — and so the framework's production gate
    doesn't fail closed."""
    assert SalesAgentProposalStore.is_durable is True


def test_committed_hold_default_seven_days():
    """v1 hold window defaults to 7 days — matches the upstream
    ``InMemoryProposalStore._DEFAULT_COMMITTED_GRACE``. Buyers have 7
    days to call ``create_media_buy(proposal_id=X)`` after receiving
    a proposal before the framework rejects with
    ``PROPOSAL_EXPIRED``. This test pins the default to prevent
    silent reductions in hold window that would surface as
    ``PROPOSAL_EXPIRED`` on real buyer flows."""
    store = SalesAgentProposalStore()
    assert store._committed_hold == timedelta(days=7)
