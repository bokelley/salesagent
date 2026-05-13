"""Integration tests for :class:`SalesAgentProposalStore`.

Exercises the Postgres-backed :class:`adcp.decisioning.ProposalStore`
implementation against a real database. The store implements the
v1.5 ``ProposalStore`` Protocol — the framework's
``proposal_dispatch`` calls into it to persist ``get_products``
proposals (as DRAFT) and resolve them on
``create_media_buy(proposal_id=X)``.

Lifecycle promotion (DRAFT → COMMITTED) is owned by the framework:
managers declaring
:attr:`ProposalCapabilities.auto_commit_on_put_draft=True` get a
synthetic :meth:`commit` call from the framework right after
:meth:`put_draft`. The store doesn't bake any lifecycle shortcuts in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.database.repositories import SalesAgentProposalStore
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration, pytest.mark.asyncio]


class _BareEnv(IntegrationEnv):
    """Minimal integration env — just session + factory binding."""

    EXTERNAL_PATCHES: dict = {}


def _make_payload(proposal_id: str = "prop_test") -> dict:
    return {
        "proposal_id": proposal_id,
        "name": "Recommended bundle",
        "allocations": [
            {"product_id": "prod_a", "allocation_percentage": 60.0},
            {"product_id": "prod_b", "allocation_percentage": 40.0},
        ],
    }


def _seven_days_from_now() -> datetime:
    """Default ``expires_at`` matching the manager's
    ``auto_commit_ttl_seconds=604800``. The framework computes
    ``expires_at`` from the capability when it calls
    ``store.commit`` after ``put_draft``; tests synthesize the same
    value directly when exercising commit out-of-band."""
    return datetime.now(UTC) + timedelta(days=7)


class TestPutDraft:
    """``put_draft`` persists in DRAFT state per spec; the framework
    owns the DRAFT → COMMITTED promotion via ``auto_commit_on_put_draft``."""

    async def test_writes_row_in_draft_state(self, integration_db):
        """The store writes spec-canonical ``draft`` — no hidden
        promotion. Managers that want the brief→create_media_buy flow
        to work without an explicit finalize step declare
        ``auto_commit_on_put_draft=True`` on their capabilities; the
        framework's dispatch calls :meth:`commit` immediately after."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_proposal_a")

            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_1",
                account_id="tenant_proposal_a",
                recipes={},
                proposal_payload=_make_payload("prop_1"),
            )

            record = await store.get("prop_1", expected_account_id="tenant_proposal_a")
            assert record is not None
            assert record.state == ProposalState.DRAFT, (
                "put_draft must write DRAFT per Protocol; DRAFT → COMMITTED is the framework's job"
            )
            assert record.expires_at is None, "DRAFT records have no hold window; commit sets expires_at"

    async def test_payload_round_trips(self, integration_db):
        """The wire ``Proposal`` payload survives persist + reload —
        :meth:`maybe_hydrate_recipes_for_create_media_buy` reads
        ``proposal_payload`` to derive packages."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_proposal_b")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_2")
            await store.put_draft(
                proposal_id="prop_2",
                account_id="tenant_proposal_b",
                recipes={},
                proposal_payload=payload,
            )
            record = await store.get("prop_2", expected_account_id="tenant_proposal_b")
            assert record is not None
            assert dict(record.proposal_payload) == payload

    async def test_refine_iteration_overwrites_existing_draft(self, integration_db):
        """``put_draft`` on an existing DRAFT record overwrites the
        payload — refine iterations re-issue the same ``proposal_id``
        and the buyer expects the latest content to win."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_proposal_c")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_3",
                account_id="tenant_proposal_c",
                recipes={},
                proposal_payload=_make_payload("prop_3"),
            )
            updated = _make_payload("prop_3")
            updated["name"] = "Updated bundle"
            await store.put_draft(
                proposal_id="prop_3",
                account_id="tenant_proposal_c",
                recipes={},
                proposal_payload=updated,
            )
            record = await store.get("prop_3", expected_account_id="tenant_proposal_c")
            assert record is not None
            assert record.proposal_payload["name"] == "Updated bundle"

    async def test_put_draft_on_committed_raises(self, integration_db):
        """Per Protocol, ``put_draft`` is only legal on DRAFT records.
        A COMMITTED proposal_id is immutable — overwrite would mean
        the buyer's prior commit/expires_at silently rolls back."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_putd_committed")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_c",
                account_id="tenant_putd_committed",
                recipes={},
                proposal_payload=_make_payload("prop_c"),
            )
            await store.commit(
                "prop_c",
                expires_at=_seven_days_from_now(),
                proposal_payload=_make_payload("prop_c"),
            )
            with pytest.raises(AdcpError) as exc:
                await store.put_draft(
                    proposal_id="prop_c",
                    account_id="tenant_putd_committed",
                    recipes={},
                    proposal_payload=_make_payload("prop_c"),
                )
            assert exc.value.code == "INTERNAL_ERROR"


class TestCommit:
    """``commit`` promotes DRAFT → COMMITTED and sets ``expires_at``."""

    async def test_commit_advances_state_and_sets_expires_at(self, integration_db):
        """The framework calls this right after :meth:`put_draft` when
        the manager declares ``auto_commit_on_put_draft=True``. The
        TTL applied here comes from
        ``ProposalCapabilities.auto_commit_ttl_seconds``."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_commit")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_commit")
            await store.put_draft(
                proposal_id="prop_commit",
                account_id="tenant_commit",
                recipes={},
                proposal_payload=payload,
            )
            expires_at = _seven_days_from_now()
            await store.commit("prop_commit", expires_at=expires_at, proposal_payload=payload)

            record = await store.get("prop_commit", expected_account_id="tenant_commit")
            assert record is not None
            assert record.state == ProposalState.COMMITTED
            assert record.expires_at == expires_at

    async def test_commit_is_idempotent_on_equal_values(self, integration_db):
        """Per Protocol: re-commit with the same ``expires_at`` +
        payload is a no-op; mismatch is an ``INTERNAL_ERROR``. The
        idempotency case lets the framework's auto-commit dispatch
        re-run safely on transient retries."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_commit_idem")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_idem")
            await store.put_draft(
                proposal_id="prop_idem",
                account_id="tenant_commit_idem",
                recipes={},
                proposal_payload=payload,
            )
            expires_at = _seven_days_from_now()
            await store.commit("prop_idem", expires_at=expires_at, proposal_payload=payload)
            # Same values — no raise.
            await store.commit("prop_idem", expires_at=expires_at, proposal_payload=payload)

    async def test_commit_rejects_changed_payload(self, integration_db):
        """Re-commit with a different payload raises ``INTERNAL_ERROR``
        — adopter / framework bug, not buyer-fixable."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_commit_drift")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_drift",
                account_id="tenant_commit_drift",
                recipes={},
                proposal_payload=_make_payload("prop_drift"),
            )
            expires_at = _seven_days_from_now()
            await store.commit(
                "prop_drift",
                expires_at=expires_at,
                proposal_payload=_make_payload("prop_drift"),
            )
            drifted = _make_payload("prop_drift")
            drifted["name"] = "Different bundle"
            with pytest.raises(AdcpError) as exc:
                await store.commit("prop_drift", expires_at=expires_at, proposal_payload=drifted)
            assert exc.value.code == "INTERNAL_ERROR"


class TestGet:
    """``get`` enforces cross-tenant probe defense."""

    async def test_cross_tenant_probe_returns_none(self, integration_db):
        """A proposal_id known to tenant A must not be visible to tenant
        B — the Protocol requires collapsing the cross-tenant probe to
        ``None`` so adversarial buyers can't enumerate proposals via
        id-guessing."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_owner")
            TenantFactory(tenant_id="tenant_probe")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_secret",
                account_id="tenant_owner",
                recipes={},
                proposal_payload=_make_payload("prop_secret"),
            )

            # Probe from the wrong tenant collapses to None.
            assert await store.get("prop_secret", expected_account_id="tenant_probe") is None
            # No expected_account_id allows admin / ops lookup.
            assert await store.get("prop_secret") is not None

    async def test_unknown_proposal_returns_none(self, integration_db):
        """Unknown ``proposal_id`` returns ``None`` — the Protocol
        contract; the framework projects this to
        ``PROPOSAL_NOT_FOUND`` at the dispatch layer."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_empty")
            store = SalesAgentProposalStore()
            assert await store.get("prop_nope", expected_account_id="tenant_empty") is None


class TestReservationLifecycle:
    """Two-phase consumption: ``committed`` → ``consuming`` → ``consumed``."""

    async def _put_and_commit(self, store: SalesAgentProposalStore, *, proposal_id: str, account_id: str) -> None:
        """Helper: put_draft + commit, the two-step the framework runs
        when ``auto_commit_on_put_draft=True``. Tests exercising
        consumption assume a COMMITTED starting state — this seeds it
        the same way the framework would."""
        payload = _make_payload(proposal_id)
        await store.put_draft(
            proposal_id=proposal_id,
            account_id=account_id,
            recipes={},
            proposal_payload=payload,
        )
        await store.commit(proposal_id, expires_at=_seven_days_from_now(), proposal_payload=payload)

    async def test_try_reserve_consumption_advances_state(self, integration_db):
        """The reservation flips the record from ``committed`` to
        ``consuming``; framework runs the adapter against this
        reservation and either finalizes (success) or releases
        (rollback)."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_reserve_a")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_reserve", account_id="tenant_reserve_a")
            reserved = await store.try_reserve_consumption("prop_reserve", expected_account_id="tenant_reserve_a")
            assert reserved.state == ProposalState.CONSUMING

    async def test_reserve_on_draft_raises_not_committed(self, integration_db):
        """A DRAFT proposal (no commit yet) must raise
        ``PROPOSAL_NOT_COMMITTED`` on reserve — sanity check that the
        store's lifecycle enforcement matches the Protocol contract
        (the prior v1 workaround skipped DRAFT entirely)."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_reserve_draft")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_unc",
                account_id="tenant_reserve_draft",
                recipes={},
                proposal_payload=_make_payload("prop_unc"),
            )
            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_unc", expected_account_id="tenant_reserve_draft")
            assert exc.value.code == "PROPOSAL_NOT_COMMITTED"

    async def test_second_reservation_raises(self, integration_db):
        """A second :meth:`try_reserve_consumption` on a reserved
        proposal raises ``PROPOSAL_NOT_COMMITTED`` — solves the
        inventory double-spend race the Protocol calls out. Two
        parallel callers cannot both reserve the same proposal."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_reserve_b")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_double", account_id="tenant_reserve_b")
            await store.try_reserve_consumption("prop_double", expected_account_id="tenant_reserve_b")
            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_double", expected_account_id="tenant_reserve_b")
            assert exc.value.code == "PROPOSAL_NOT_COMMITTED"

    async def test_reserve_cross_tenant_returns_not_found(self, integration_db):
        """Cross-tenant probe on :meth:`try_reserve_consumption`
        collapses to ``PROPOSAL_NOT_FOUND`` — same defense as
        :meth:`get`, since this method is reachable via the framework's
        ``create_media_buy(proposal_id=X)`` dispatch and proposal_ids
        are buyer-controllable."""
        from adcp.decisioning.types import AdcpError

        with _BareEnv():
            TenantFactory(tenant_id="tenant_owner_r")
            TenantFactory(tenant_id="tenant_probe_r")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_cross", account_id="tenant_owner_r")
            with pytest.raises(AdcpError) as exc:
                await store.try_reserve_consumption("prop_cross", expected_account_id="tenant_probe_r")
            assert exc.value.code == "PROPOSAL_NOT_FOUND"

    async def test_finalize_records_media_buy_id(self, integration_db):
        """Successful adapter dispatch finalizes the reservation —
        ``state`` becomes ``consumed`` and ``media_buy_id`` is recorded
        for reverse-index lookup via :meth:`get_by_media_buy_id`."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_finalize")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_final", account_id="tenant_finalize")
            await store.try_reserve_consumption("prop_final", expected_account_id="tenant_finalize")
            await store.finalize_consumption("prop_final", media_buy_id="mb_123", expected_account_id="tenant_finalize")
            record = await store.get("prop_final", expected_account_id="tenant_finalize")
            assert record is not None
            assert record.state == ProposalState.CONSUMED
            assert record.media_buy_id == "mb_123"

    async def test_release_rolls_back_to_committed(self, integration_db):
        """Adapter failure releases the reservation back to
        ``committed`` so the buyer can retry without
        ``PROPOSAL_NOT_COMMITTED``."""
        from adcp.decisioning.proposal_store import ProposalState

        with _BareEnv():
            TenantFactory(tenant_id="tenant_release")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_release", account_id="tenant_release")
            await store.try_reserve_consumption("prop_release", expected_account_id="tenant_release")
            await store.release_consumption("prop_release", expected_account_id="tenant_release")
            record = await store.get("prop_release", expected_account_id="tenant_release")
            assert record is not None
            assert record.state == ProposalState.COMMITTED, (
                "release must roll back to COMMITTED so the buyer's retry succeeds"
            )

    async def test_release_on_committed_is_idempotent(self, integration_db):
        """Releasing a record already in ``committed`` is a no-op so
        the adapter-failure rollback path can fire unconditionally."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_idem")
            store = SalesAgentProposalStore()
            await self._put_and_commit(store, proposal_id="prop_idem", account_id="tenant_idem")
            # Never reserved — release should no-op without raising.
            await store.release_consumption("prop_idem", expected_account_id="tenant_idem")


class TestReverseIndex:
    """``get_by_media_buy_id`` requires ``expected_account_id`` per Protocol."""

    async def test_resolves_consumed_proposal_for_media_buy(self, integration_db):
        """After finalize, the proposal is reachable via the consumed
        ``media_buy_id`` — used by audit / debug flows that have a
        media buy and want to recover the proposal context."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_reverse")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_rev")
            await store.put_draft(
                proposal_id="prop_rev",
                account_id="tenant_reverse",
                recipes={},
                proposal_payload=payload,
            )
            await store.commit("prop_rev", expires_at=_seven_days_from_now(), proposal_payload=payload)
            await store.try_reserve_consumption("prop_rev", expected_account_id="tenant_reverse")
            await store.finalize_consumption("prop_rev", media_buy_id="mb_rev", expected_account_id="tenant_reverse")
            record = await store.get_by_media_buy_id("mb_rev", expected_account_id="tenant_reverse")
            assert record is not None
            assert record.proposal_id == "prop_rev"

    async def test_cross_tenant_reverse_lookup_returns_none(self, integration_db):
        """Reverse lookup with the wrong ``expected_account_id`` returns
        ``None`` — guards against collisions across tenants when
        ``media_buy_id`` sequences overlap (e.g., deterministic test
        fixtures, sequential numeric IDs)."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_rev_owner")
            TenantFactory(tenant_id="tenant_rev_probe")
            store = SalesAgentProposalStore()
            payload = _make_payload("prop_rev_secret")
            await store.put_draft(
                proposal_id="prop_rev_secret",
                account_id="tenant_rev_owner",
                recipes={},
                proposal_payload=payload,
            )
            await store.commit("prop_rev_secret", expires_at=_seven_days_from_now(), proposal_payload=payload)
            await store.try_reserve_consumption("prop_rev_secret", expected_account_id="tenant_rev_owner")
            await store.finalize_consumption(
                "prop_rev_secret",
                media_buy_id="mb_shared",
                expected_account_id="tenant_rev_owner",
            )
            assert (await store.get_by_media_buy_id("mb_shared", expected_account_id="tenant_rev_probe")) is None


class TestDiscard:
    """Idempotent delete — unknown ids no-op so caller doesn't need to
    branch on existence."""

    async def test_discard_unknown_is_noop(self, integration_db):
        """``discard`` on an unknown ``proposal_id`` returns cleanly —
        the Protocol contract says rollback paths use this method
        unconditionally."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_discard_a")
            store = SalesAgentProposalStore()
            await store.discard("prop_does_not_exist")  # no raise

    async def test_discard_removes_record(self, integration_db):
        """``discard`` deletes the record so subsequent
        :meth:`get` returns ``None``."""
        with _BareEnv():
            TenantFactory(tenant_id="tenant_discard_b")
            store = SalesAgentProposalStore()
            await store.put_draft(
                proposal_id="prop_to_discard",
                account_id="tenant_discard_b",
                recipes={},
                proposal_payload=_make_payload("prop_to_discard"),
            )
            await store.discard("prop_to_discard")
            assert await store.get("prop_to_discard", expected_account_id="tenant_discard_b") is None
