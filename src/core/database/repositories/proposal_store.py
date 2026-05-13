"""Postgres-backed :class:`adcp.decisioning.ProposalStore` implementation.

Implements the v1.5 ``ProposalStore`` Protocol against the
:class:`src.core.database.models.Proposal` table so the framework's
``proposal_dispatch`` can persist proposals from ``get_products`` and
resolve them on ``create_media_buy(proposal_id=X)``. Without a wired
store, every brief→create_media_buy storyboard flow fails with
``INVALID_REQUEST: Invalid budget: 0.0`` because the framework has no
way to look up the prior proposal and derive packages from its
allocations.

Multi-tenancy: a single ``SalesAgentProposalStore`` instance is shared
across every tenant — the framework passes ``expected_account_id`` on
every read (the AdCP account id, which the salesagent maps 1-1 with
``tenant_id``). Cross-tenant probes collapse to ``None`` per the
Protocol's defense against principal-enumeration via ``proposal_id``
guessing.

v1 lifecycle compromise: ``put_draft`` writes the row in ``committed``
state with a 7-day ``expires_at`` instead of ``draft``. The
storyboard's ``proposal_finalize`` flow goes brief → create_media_buy
WITHOUT an explicit finalize call, but the framework's
:meth:`try_reserve_consumption` requires ``committed``. Auto-committing
at issuance unblocks the flow today; when the manager declares
``finalize=True`` (v2) the store will swap to canonical
``draft`` + explicit commit. The lifecycle compromise is internal to
the store — the Protocol surface (``put_draft`` / ``commit`` /
``try_reserve_consumption`` / etc.) is unchanged from the spec.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Proposal as ProposalRow

if TYPE_CHECKING:
    from adcp.decisioning.proposal_store import ProposalRecord
    from adcp.decisioning.recipe import Recipe

logger = logging.getLogger(__name__)


#: Default hold window for v1 auto-committed proposals. The framework
#: rejects ``try_reserve_consumption`` past ``expires_at`` (plus the
#: adopter's grace window). 7 days matches the upstream
#: ``InMemoryProposalStore`` ``_DEFAULT_COMMITTED_GRACE``.
_DEFAULT_COMMITTED_HOLD = timedelta(days=7)


def _to_record(row: ProposalRow) -> ProposalRecord:
    """Convert a :class:`ProposalRow` ORM instance to the framework's
    :class:`ProposalRecord` dataclass. The framework only reads
    ``ProposalRecord`` — never writes back through the Protocol — so
    the conversion is unidirectional."""
    # Lazy import: keeps the framework type out of module-load
    # circular-import risk (the store is wired in
    # ``core/main.py:build_router`` which imports the adcp library).
    from adcp.decisioning.proposal_store import ProposalRecord, ProposalState

    return ProposalRecord(
        proposal_id=row.proposal_id,
        account_id=row.account_id,
        state=ProposalState(row.state),
        recipes=dict(row.recipes or {}),
        proposal_payload=dict(row.proposal_payload or {}),
        expires_at=row.expires_at,
        media_buy_id=row.media_buy_id,
        recipe_schema_version=row.recipe_schema_version,
    )


def _resolve_tenant_id_for_account(session, account_id: str) -> str:
    """Map an AdCP account_id to a salesagent ``tenant_id``.

    The salesagent's :class:`SalesagentAccountStore` maps tenant rows
    1:1 with AdCP accounts — ``account_id`` IS the ``tenant_id`` value
    today (see :class:`core.stores.accounts.SalesagentAccountStore`).
    Lookups via the Account store are the source of truth, but the
    1:1 mapping is stable enough that we can short-circuit. If the
    mapping ever diverges (e.g., one tenant hosts multiple accounts),
    this resolver swaps to a real lookup without changing the store
    surface.
    """
    return account_id


def _serialize_recipes(recipes: Mapping[str, Recipe]) -> dict[str, Any]:
    """Project the typed :class:`Recipe` mapping to a JSON-serializable
    dict for storage.

    v1 stores empty dict (our :class:`SalesAgentProposalManager`
    doesn't yet attach typed recipes to products). v2 will hydrate
    Recipe model_dump payloads here; the store reads them back as
    plain dicts (the framework re-validates on read if needed).
    """
    if not recipes:
        return {}
    out: dict[str, Any] = {}
    for product_id, recipe in recipes.items():
        # ``Recipe`` is a pydantic model; ``model_dump`` gives a
        # round-trippable dict. The store keeps the data shape-agnostic
        # so future Recipe subclasses don't need a store migration.
        out[str(product_id)] = recipe.model_dump(mode="json") if hasattr(recipe, "model_dump") else recipe
    return out


class SalesAgentProposalStore:
    """Postgres-backed :class:`adcp.decisioning.proposal_store.ProposalStore`.

    Wired into :class:`core.main._LazyPlatformRouterWithStore` as the
    single shared store across every tenant. The framework's
    ``proposal_dispatch`` calls into this instance via the router's
    :meth:`proposal_store_for_tenant` accessor.

    Concurrency: each method opens a short-lived session via
    :func:`get_db_session`; cross-method state isn't shared. Atomic
    CAS operations (:meth:`try_reserve_consumption`,
    :meth:`finalize_consumption`) use ``SELECT … FOR UPDATE`` to
    serialize against parallel callers — two concurrent
    ``create_media_buy(proposal_id=X)`` calls produce exactly one
    successful reservation per the Protocol contract.
    """

    #: The Protocol's production-mode gate reads this attribute. ``True``
    #: signals the framework that the store is durable (won't lose
    #: in-flight proposals on worker rotation).
    is_durable: ClassVar[bool] = True

    def __init__(self, *, committed_hold: timedelta = _DEFAULT_COMMITTED_HOLD) -> None:
        """Construct the store.

        :param committed_hold: Default hold window applied to
            v1-auto-committed proposals at ``put_draft`` time. The
            framework rejects ``create_media_buy(proposal_id=X)`` past
            this deadline. 7 days matches the upstream reference impl.
        """
        self._committed_hold = committed_hold

    async def put_draft(
        self,
        *,
        proposal_id: str,
        account_id: str,
        recipes: Mapping[str, Recipe],
        proposal_payload: Mapping[str, Any],
    ) -> None:
        """Persist a proposal so :meth:`create_media_buy(proposal_id=X)`
        can resolve it later.

        v1 compromise: writes the row in ``committed`` state with a
        7-day ``expires_at`` (instead of ``draft`` per spec). The
        storyboard flow brief → create_media_buy has no intermediate
        finalize step today; auto-committing unblocks
        ``try_reserve_consumption`` without forcing the buyer to issue
        a finalize call. Switch to canonical ``draft`` when the manager
        declares ``finalize=True`` in v2.

        Idempotent on the same ``proposal_id``: refine iterations
        overwrite the prior payload + recipes but preserve
        ``created_at`` (and the v1 hold deadline anchors to the most
        recent put — refine extends the hold).
        """
        from adcp.decisioning.proposal_store import ProposalState

        now = datetime.now(UTC)
        recipes_json = _serialize_recipes(recipes)
        payload_dict = dict(proposal_payload)

        with get_db_session() as session:
            existing = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()

            if existing is not None:
                # Reject overwrite of a consumed proposal — the buyer
                # has already used this proposal_id to create a media
                # buy. Letting refine overwrite would silently strand
                # the consumed buy's recipe linkage.
                if existing.state == ProposalState.CONSUMED.value:
                    from adcp.decisioning.types import AdcpError

                    raise AdcpError(
                        "INTERNAL_ERROR",
                        message=(
                            f"Cannot put_draft on proposal {proposal_id!r}: "
                            f"already consumed by media_buy_id="
                            f"{existing.media_buy_id!r}."
                        ),
                        recovery="terminal",
                    )
                existing.account_id = account_id
                existing.state = ProposalState.COMMITTED.value
                existing.recipes = recipes_json
                existing.proposal_payload = payload_dict
                existing.expires_at = now + self._committed_hold
                # tenant_id stays pinned to the original tenant — refine
                # within a tenant is fine; cross-tenant overwrite would
                # mean a colliding proposal_id, which our id mint
                # (``prop_{uuid4_hex[:12]}``) makes vanishingly unlikely.
                session.commit()
                return

            tenant_id = _resolve_tenant_id_for_account(session, account_id)
            row = ProposalRow(
                proposal_id=proposal_id,
                tenant_id=tenant_id,
                account_id=account_id,
                state=ProposalState.COMMITTED.value,
                recipes=recipes_json,
                proposal_payload=payload_dict,
                expires_at=now + self._committed_hold,
            )
            session.add(row)
            session.commit()

    async def get(
        self,
        proposal_id: str,
        *,
        expected_account_id: str | None = None,
    ) -> ProposalRecord | None:
        """Look up a proposal; cross-tenant probes return ``None``."""
        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id)).first()
            if row is None:
                return None
            if expected_account_id is not None and row.account_id != expected_account_id:
                # Cross-tenant probe defense — return None, never the
                # raw record. Mirrors the Protocol docstring's example
                # and the InMemory reference impl's posture.
                return None
            return _to_record(row)

    async def commit(
        self,
        proposal_id: str,
        *,
        expires_at: datetime,
        proposal_payload: Mapping[str, Any],
    ) -> None:
        """Promote ``draft`` → ``committed``.

        Idempotent on re-call with equal ``expires_at`` + payload — a
        second commit with different values raises ``INTERNAL_ERROR``
        (adopter / framework bug, not buyer-fixable).
        """
        from adcp.decisioning.proposal_store import ProposalState
        from adcp.decisioning.types import AdcpError

        payload_dict = dict(proposal_payload)
        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()
            if row is None:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"Cannot commit proposal {proposal_id!r}: not in "
                        "store. The framework's finalize dispatch must "
                        "put_draft before commit."
                    ),
                    recovery="terminal",
                )
            if row.state == ProposalState.COMMITTED.value:
                same_deadline = row.expires_at == expires_at
                same_payload = dict(row.proposal_payload or {}) == payload_dict
                if same_deadline and same_payload:
                    return
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"Proposal {proposal_id!r} already committed with a "
                        "different expires_at or payload — re-commit with "
                        "different values is a developer bug."
                    ),
                    recovery="terminal",
                )
            if row.state != ProposalState.DRAFT.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"Cannot commit proposal {proposal_id!r} from state {row.state!r}; commit requires DRAFT."
                    ),
                    recovery="terminal",
                )
            row.state = ProposalState.COMMITTED.value
            row.expires_at = expires_at
            row.proposal_payload = payload_dict
            session.commit()

    async def try_reserve_consumption(
        self,
        proposal_id: str,
        *,
        expected_account_id: str,
    ) -> ProposalRecord:
        """Atomic CAS ``committed`` → ``consuming``.

        Serializes parallel ``create_media_buy(proposal_id=X)`` callers
        via ``SELECT … FOR UPDATE``. The loser raises
        ``PROPOSAL_NOT_COMMITTED`` per the Protocol — same outcome the
        InMemory reference impl produces from its asyncio.Lock.
        """
        from adcp.decisioning.proposal_store import ProposalState
        from adcp.decisioning.types import AdcpError

        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()
            if row is None or row.account_id != expected_account_id:
                # Cross-tenant probe collapses to PROPOSAL_NOT_FOUND —
                # never disclose existence of another tenant's record.
                raise AdcpError(
                    "PROPOSAL_NOT_FOUND",
                    message=f"Proposal {proposal_id!r} not found.",
                    recovery="terminal",
                    field="proposal_id",
                )
            if row.state != ProposalState.COMMITTED.value:
                raise AdcpError(
                    "PROPOSAL_NOT_COMMITTED",
                    message=(
                        f"Proposal {proposal_id!r} is in state "
                        f"{row.state!r}; create_media_buy requires a "
                        "committed proposal that hasn't been accepted "
                        "or reserved by another request."
                    ),
                    recovery="correctable",
                    field="proposal_id",
                )
            row.state = ProposalState.CONSUMING.value
            session.commit()
            session.refresh(row)
            return _to_record(row)

    async def finalize_consumption(
        self,
        proposal_id: str,
        *,
        media_buy_id: str,
        expected_account_id: str,
    ) -> None:
        """Promote ``consuming`` → ``consumed`` and record the
        ``media_buy_id`` back-reference for
        :meth:`get_by_media_buy_id` lookups.
        """
        from adcp.decisioning.proposal_store import ProposalState
        from adcp.decisioning.types import AdcpError

        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()
            if row is None or row.account_id != expected_account_id:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(f"finalize_consumption: proposal {proposal_id!r} not found for the expected tenant."),
                    recovery="terminal",
                )
            if row.state == ProposalState.CONSUMED.value:
                # Idempotent on re-finalize with the same media_buy_id;
                # mismatch is a framework bug (two media buys claiming
                # the same proposal).
                if row.media_buy_id == media_buy_id:
                    return
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"Proposal {proposal_id!r} already consumed by "
                        f"media_buy_id={row.media_buy_id!r}; cannot "
                        f"re-consume as {media_buy_id!r}."
                    ),
                    recovery="terminal",
                )
            if row.state != ProposalState.CONSUMING.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(f"finalize_consumption requires CONSUMING; proposal {proposal_id!r} is in {row.state!r}."),
                    recovery="terminal",
                )
            row.state = ProposalState.CONSUMED.value
            row.media_buy_id = media_buy_id
            session.commit()

    async def release_consumption(
        self,
        proposal_id: str,
        *,
        expected_account_id: str,
    ) -> None:
        """Rollback ``consuming`` → ``committed`` so the buyer can retry.

        Idempotent on a record already in ``committed`` (another
        rollback path may have run); unknown ids are also a no-op so
        the adapter-failure rollback can be unconditional.
        """
        from adcp.decisioning.proposal_store import ProposalState
        from adcp.decisioning.types import AdcpError

        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()
            if row is None or row.account_id != expected_account_id:
                return
            if row.state == ProposalState.COMMITTED.value:
                return
            if row.state != ProposalState.CONSUMING.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(f"release_consumption requires CONSUMING; proposal {proposal_id!r} is in {row.state!r}."),
                    recovery="terminal",
                )
            row.state = ProposalState.COMMITTED.value
            session.commit()

    async def mark_consumed(
        self,
        proposal_id: str,
        *,
        media_buy_id: str,
    ) -> None:
        """Legacy direct ``committed`` → ``consumed`` for v1.5 alpha
        compatibility. Equivalent to ``try_reserve_consumption`` +
        ``finalize_consumption`` against a single-threaded write; new
        dispatch code uses the two-phase methods.
        """
        from adcp.decisioning.proposal_store import ProposalState
        from adcp.decisioning.types import AdcpError

        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()
            if row is None:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=f"Cannot mark_consumed proposal {proposal_id!r}: not in store.",
                    recovery="terminal",
                )
            if row.state == ProposalState.CONSUMED.value:
                if row.media_buy_id == media_buy_id:
                    return
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"Proposal {proposal_id!r} already consumed by "
                        f"media_buy_id={row.media_buy_id!r}; cannot "
                        f"re-consume as {media_buy_id!r}."
                    ),
                    recovery="terminal",
                )
            if row.state != ProposalState.COMMITTED.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"Cannot mark_consumed proposal {proposal_id!r} "
                        f"from state {row.state!r}; mark_consumed "
                        "requires COMMITTED."
                    ),
                    recovery="terminal",
                )
            row.state = ProposalState.CONSUMED.value
            row.media_buy_id = media_buy_id
            session.commit()

    async def discard(self, proposal_id: str) -> None:
        """Idempotent delete — unknown ids no-op."""
        with get_db_session() as session:
            row = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id)).first()
            if row is not None:
                session.delete(row)
                session.commit()

    async def get_by_media_buy_id(
        self,
        media_buy_id: str,
        *,
        expected_account_id: str,
    ) -> ProposalRecord | None:
        """Reverse-index lookup — hydrate the consumed proposal that
        produced ``media_buy_id`` for the given tenant.

        ``expected_account_id`` is required (no default) per the
        Protocol — ``media_buy_id`` is adopter-controlled and can
        collide across tenants (sequential IDs, deterministic test
        fixtures). The ``(account_id, media_buy_id)`` partial unique
        index enforces the tenant-scoped uniqueness this lookup
        depends on.
        """
        with get_db_session() as session:
            row = session.scalars(
                select(ProposalRow).filter_by(
                    account_id=expected_account_id,
                    media_buy_id=media_buy_id,
                )
            ).first()
            if row is None:
                return None
            return _to_record(row)
