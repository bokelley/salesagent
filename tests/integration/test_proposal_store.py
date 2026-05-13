"""``SalesAgentProposalStore`` end-to-end against real Postgres.

Closes bokelley/salesagent#387: persists proposals across
``put_draft → commit → try_reserve_consumption → finalize_consumption``
so the storyboard
``media_buy_seller/proposal_finalize/create_media_buy`` can hydrate
the buyer's ``proposal_id`` reference after a prior ``get_products``
mint. Per the upstream Protocol (adcp 5.4.0
``adcp.decisioning.proposal_store.ProposalStore``), every transition
is row-locked so two parallel ``try_reserve_consumption`` callers
can't both reserve.

All tests are integration tests — they exercise the SQL through real
SQLAlchemy + Postgres. Sync method shape (no asyncio) per the
``MaybeAsync`` Protocol contract; the framework awaits via
``_await_maybe`` at dispatch time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from adcp.decisioning import AdcpError
from adcp.decisioning.proposal_store import ProposalState
from adcp.decisioning.recipe import Recipe

from core.proposal.store import SalesAgentProposalStore
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv


class _ProposalStoreEnv(IntegrationEnv):
    """Minimal integration env — binds the SQLAlchemy session to
    factory-boy so ``TenantFactory()`` doesn't raise ``No session
    provided``. Mirrors ``_AccountEnv`` in ``test_account_model.py``.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}


def _make_recipe() -> Recipe:
    """Minimal valid :class:`Recipe` for store roundtrip tests.

    The :class:`adcp.decisioning.recipe.Recipe` shape today is a
    near-empty Pydantic model (just ``capability_overlap``); store-
    layer tests don't need real allocation data — they need a value
    the JSON column can round-trip. The framework re-hydrates via
    ``Recipe.model_validate`` on read.
    """
    return Recipe()


@pytest.fixture
def proposal_store(integration_db) -> SalesAgentProposalStore:
    """Per-test store instance bound to a fresh tenant.

    Uses :class:`_ProposalStoreEnv` to bind a SQLAlchemy session to
    factory-boy. The store itself opens its own sessions via
    ``get_db_session`` — the harness session is only for tenant
    fixture creation, not for the store under test.
    """
    with _ProposalStoreEnv() as env:
        tenant = TenantFactory()
        env._commit_factory_data()  # flush the tenant row before the store opens its own session
        yield SalesAgentProposalStore(tenant_id=tenant.tenant_id)


@pytest.mark.requires_db
class TestPutDraftAndGet:
    """Round-trip a DRAFT proposal."""

    def test_put_then_get_returns_record(self, proposal_store: SalesAgentProposalStore) -> None:
        recipes = {"prod_a": _make_recipe(), "prod_b": _make_recipe()}
        payload = {"name": "Recommended bundle", "allocations": [{"product_id": "prod_a", "pct": 60}]}

        proposal_store.put_draft(
            proposal_id="prop_001",
            account_id="acct_acme",
            recipes=recipes,
            proposal_payload=payload,
        )

        record = proposal_store.get("prop_001", expected_account_id="acct_acme")

        assert record is not None
        assert record.proposal_id == "prop_001"
        assert record.account_id == "acct_acme"
        assert record.state == ProposalState.DRAFT
        assert set(record.recipes) == {"prod_a", "prod_b"}
        assert dict(record.proposal_payload) == payload
        assert record.media_buy_id is None
        assert record.expires_at is None

    def test_overwrite_while_draft(self, proposal_store: SalesAgentProposalStore) -> None:
        """Refine iterations call put_draft with the same proposal_id."""
        recipes = {"prod_a": _make_recipe()}
        proposal_store.put_draft(
            proposal_id="prop_002", account_id="acct_a", recipes=recipes, proposal_payload={"v": 1}
        )
        proposal_store.put_draft(
            proposal_id="prop_002", account_id="acct_a", recipes=recipes, proposal_payload={"v": 2}
        )

        record = proposal_store.get("prop_002", expected_account_id="acct_a")
        assert record is not None
        assert record.proposal_payload["v"] == 2

    def test_overwrite_after_commit_raises(self, proposal_store: SalesAgentProposalStore) -> None:
        """Post-COMMITTED ``put_draft`` is rejected — refine iterations
        only legal in DRAFT."""
        recipes = {"prod_a": _make_recipe()}
        proposal_store.put_draft(
            proposal_id="prop_003", account_id="acct_a", recipes=recipes, proposal_payload={"v": 1}
        )
        proposal_store.commit("prop_003", expires_at=datetime.now(UTC) + timedelta(days=7), proposal_payload={"v": 1})

        with pytest.raises(AdcpError) as exc_info:
            proposal_store.put_draft(
                proposal_id="prop_003", account_id="acct_a", recipes=recipes, proposal_payload={"v": 99}
            )
        assert exc_info.value.code == "INTERNAL_ERROR"
        assert "DRAFT" in str(exc_info.value)

    def test_get_cross_account_returns_none(self, proposal_store: SalesAgentProposalStore) -> None:
        """Account mismatch returns None, not the raw record — the
        Protocol's tenant-isolation invariant. Otherwise a buyer with
        a guessable proposal_id could enumerate cross-account
        proposals."""
        proposal_store.put_draft(
            proposal_id="prop_004",
            account_id="acct_owner",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={},
        )
        assert proposal_store.get("prop_004", expected_account_id="acct_other") is None
        # Without expected_account_id, the lookup succeeds.
        assert proposal_store.get("prop_004") is not None

    def test_get_unknown_returns_none(self, proposal_store: SalesAgentProposalStore) -> None:
        assert proposal_store.get("prop_does_not_exist", expected_account_id="acct_a") is None


@pytest.mark.requires_db
class TestCommitLifecycle:
    """DRAFT → COMMITTED transition + idempotency."""

    def test_commit_promotes_to_committed(self, proposal_store: SalesAgentProposalStore) -> None:
        expires = datetime.now(UTC) + timedelta(days=7)
        proposal_store.put_draft(
            proposal_id="prop_010",
            account_id="acct_a",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={"v": 1},
        )
        proposal_store.commit("prop_010", expires_at=expires, proposal_payload={"v": 1})

        record = proposal_store.get("prop_010", expected_account_id="acct_a")
        assert record is not None
        assert record.state == ProposalState.COMMITTED
        assert record.expires_at == expires

    def test_commit_idempotent_on_matching_args(self, proposal_store: SalesAgentProposalStore) -> None:
        """A second commit with identical (expires_at, payload) is a no-op."""
        expires = datetime.now(UTC) + timedelta(days=7)
        proposal_store.put_draft(
            proposal_id="prop_011",
            account_id="acct_a",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={"v": 1},
        )
        proposal_store.commit("prop_011", expires_at=expires, proposal_payload={"v": 1})
        # Second call must not raise.
        proposal_store.commit("prop_011", expires_at=expires, proposal_payload={"v": 1})

    def test_commit_mismatch_raises_internal_error(self, proposal_store: SalesAgentProposalStore) -> None:
        """Second commit with different args is a framework bug, not a buyer error."""
        expires_a = datetime.now(UTC) + timedelta(days=7)
        expires_b = datetime.now(UTC) + timedelta(days=14)
        proposal_store.put_draft(
            proposal_id="prop_012",
            account_id="acct_a",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={"v": 1},
        )
        proposal_store.commit("prop_012", expires_at=expires_a, proposal_payload={"v": 1})

        with pytest.raises(AdcpError) as exc_info:
            proposal_store.commit("prop_012", expires_at=expires_b, proposal_payload={"v": 1})
        assert exc_info.value.code == "INTERNAL_ERROR"

    def test_commit_unknown_raises_proposal_not_found(self, proposal_store: SalesAgentProposalStore) -> None:
        with pytest.raises(AdcpError) as exc_info:
            proposal_store.commit(
                "prop_unknown",
                expires_at=datetime.now(UTC) + timedelta(days=7),
                proposal_payload={},
            )
        assert exc_info.value.code == "PROPOSAL_NOT_FOUND"


@pytest.mark.requires_db
class TestConsumptionLifecycle:
    """COMMITTED → CONSUMING → CONSUMED + rollback path."""

    def _committed(self, store: SalesAgentProposalStore, proposal_id: str = "prop_020") -> None:
        store.put_draft(
            proposal_id=proposal_id,
            account_id="acct_a",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={"v": 1},
        )
        store.commit(proposal_id, expires_at=datetime.now(UTC) + timedelta(days=7), proposal_payload={"v": 1})

    def test_reserve_promotes_to_consuming(self, proposal_store: SalesAgentProposalStore) -> None:
        self._committed(proposal_store)
        record = proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        assert record.state == ProposalState.CONSUMING

    def test_reserve_cross_account_raises_not_found(self, proposal_store: SalesAgentProposalStore) -> None:
        """Cross-account probe must not leak existence — surface as
        PROPOSAL_NOT_FOUND, not PROPOSAL_NOT_COMMITTED."""
        self._committed(proposal_store)
        with pytest.raises(AdcpError) as exc_info:
            proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_b")
        assert exc_info.value.code == "PROPOSAL_NOT_FOUND"

    def test_reserve_twice_second_raises_not_committed(self, proposal_store: SalesAgentProposalStore) -> None:
        """The race-safe CAS: a second reserve against a CONSUMING record
        loses with PROPOSAL_NOT_COMMITTED — the framework documents this as
        the two-phase commit's race-loser path."""
        self._committed(proposal_store)
        proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        with pytest.raises(AdcpError) as exc_info:
            proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        assert exc_info.value.code == "PROPOSAL_NOT_COMMITTED"

    def test_finalize_promotes_to_consumed(self, proposal_store: SalesAgentProposalStore) -> None:
        self._committed(proposal_store)
        proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        proposal_store.finalize_consumption("prop_020", media_buy_id="mb_001", expected_account_id="acct_a")

        record = proposal_store.get("prop_020", expected_account_id="acct_a")
        assert record is not None
        assert record.state == ProposalState.CONSUMED
        assert record.media_buy_id == "mb_001"

    def test_release_rolls_back_to_committed(self, proposal_store: SalesAgentProposalStore) -> None:
        """Rollback path — adapter dispatch raised, retry must be legal."""
        self._committed(proposal_store)
        proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        proposal_store.release_consumption("prop_020", expected_account_id="acct_a")

        record = proposal_store.get("prop_020", expected_account_id="acct_a")
        assert record is not None
        assert record.state == ProposalState.COMMITTED
        # Retry succeeds — proves the rollback fully restored COMMITTED.
        record = proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        assert record.state == ProposalState.CONSUMING

    def test_release_idempotent_on_committed(self, proposal_store: SalesAgentProposalStore) -> None:
        """Idempotency: a second release against COMMITTED is a no-op."""
        self._committed(proposal_store)
        proposal_store.try_reserve_consumption("prop_020", expected_account_id="acct_a")
        proposal_store.release_consumption("prop_020", expected_account_id="acct_a")
        # Second release: must NOT raise.
        proposal_store.release_consumption("prop_020", expected_account_id="acct_a")


@pytest.mark.requires_db
class TestReverseIndex:
    """``get_by_media_buy_id`` reverse lookup."""

    def test_reverse_lookup_after_finalize(self, proposal_store: SalesAgentProposalStore) -> None:
        proposal_store.put_draft(
            proposal_id="prop_030",
            account_id="acct_a",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={},
        )
        proposal_store.commit("prop_030", expires_at=datetime.now(UTC) + timedelta(days=7), proposal_payload={})
        proposal_store.try_reserve_consumption("prop_030", expected_account_id="acct_a")
        proposal_store.finalize_consumption("prop_030", media_buy_id="mb_030", expected_account_id="acct_a")

        record = proposal_store.get_by_media_buy_id("mb_030", expected_account_id="acct_a")
        assert record is not None
        assert record.proposal_id == "prop_030"
        assert record.state == ProposalState.CONSUMED

    def test_reverse_lookup_cross_account_returns_none(self, proposal_store: SalesAgentProposalStore) -> None:
        proposal_store.put_draft(
            proposal_id="prop_031",
            account_id="acct_owner",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={},
        )
        proposal_store.commit("prop_031", expires_at=datetime.now(UTC) + timedelta(days=7), proposal_payload={})
        proposal_store.try_reserve_consumption("prop_031", expected_account_id="acct_owner")
        proposal_store.finalize_consumption("prop_031", media_buy_id="mb_031", expected_account_id="acct_owner")

        # Different account asking for the same media_buy_id — return None.
        assert proposal_store.get_by_media_buy_id("mb_031", expected_account_id="acct_other") is None

    def test_reverse_lookup_unknown_returns_none(self, proposal_store: SalesAgentProposalStore) -> None:
        assert proposal_store.get_by_media_buy_id("mb_unknown", expected_account_id="acct_a") is None


@pytest.mark.requires_db
class TestDiscard:
    """``discard`` is idempotent."""

    def test_discard_removes_row(self, proposal_store: SalesAgentProposalStore) -> None:
        proposal_store.put_draft(
            proposal_id="prop_040",
            account_id="acct_a",
            recipes={"prod_a": _make_recipe()},
            proposal_payload={},
        )
        proposal_store.discard("prop_040")
        assert proposal_store.get("prop_040") is None

    def test_discard_idempotent_on_missing(self, proposal_store: SalesAgentProposalStore) -> None:
        """Discarding an unknown id is a no-op — must not raise."""
        proposal_store.discard("prop_does_not_exist")
        assert proposal_store.get("prop_does_not_exist") is None


@pytest.mark.requires_db
class TestProtocolMetadata:
    """``is_durable=True`` drives the framework's production-mode gate."""

    def test_is_durable_class_attribute(self) -> None:
        assert SalesAgentProposalStore.is_durable is True
