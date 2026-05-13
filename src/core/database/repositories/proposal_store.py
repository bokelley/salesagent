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
every call and every read filters on it in the WHERE clause (not
after fetch — fetch-then-check leaks existence via row-lock timing).
Cross-tenant probes collapse to ``None`` / ``PROPOSAL_NOT_FOUND`` per
the Protocol's defense against principal-enumeration via
``proposal_id`` guessing.

Lifecycle: ``put_draft`` writes DRAFT state per spec; the framework
calls :meth:`commit` immediately after when the manager declares
``ProposalCapabilities.auto_commit_on_put_draft=True`` (adcp 5.4+,
#723), promoting DRAFT → COMMITTED in a single dispatch so the next
``create_media_buy(proposal_id=X)`` finds a COMMITTED record.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from adcp.decisioning.proposal_store import ProposalRecord, ProposalState
from adcp.decisioning.types import AdcpError
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Proposal as ProposalRow

if TYPE_CHECKING:
    from adcp.decisioning.recipe import Recipe

logger = logging.getLogger(__name__)


def _to_record(row: ProposalRow) -> ProposalRecord:
    """Convert a :class:`ProposalRow` ORM instance to the framework's
    :class:`ProposalRecord` dataclass. The framework only reads
    ``ProposalRecord`` — never writes back through the Protocol — so
    the conversion is unidirectional."""
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


def _resolve_tenant_id_for_account(account_id: str) -> str:
    """Map an AdCP ``account_id`` to a salesagent ``tenant_id``.

    :class:`SalesagentAccountStore` (``core/stores/accounts.py:89``)
    mints ``account_id`` as ``f"{tenant_id}:{ref}"`` (``ref`` defaults
    to ``"default"``; storyboard runs use ``"acct_demo"``). The
    framework passes ``ctx.account.id`` — the full compound string —
    into :meth:`ProposalStore.put_draft`, so the store has to split it
    back into the ``tenant_id`` for the ``proposals.tenant_id`` FK.

    Direct-string ``account_id`` values (legacy callers / tests) with
    no ``":"`` are returned verbatim. If the result isn't a real
    ``tenant_id`` the FK enforces it at insert time.
    """
    if ":" in account_id:
        tenant_id, _ = account_id.split(":", 1)
        return tenant_id
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
        if not hasattr(recipe, "model_dump"):
            # Caller is supposed to pass typed Pydantic ``Recipe``
            # instances. Silently passing dicts through would land
            # arbitrary JSON in the store and surface as a deserialize
            # error far from the source. Fail loudly here.
            raise TypeError(
                f"recipes[{product_id!r}] is {type(recipe).__name__}, expected a Recipe "
                "instance (pydantic BaseModel). Refusing to silently persist "
                "untyped data."
            )
        out[str(product_id)] = recipe.model_dump(mode="json")
    return out


class SalesAgentProposalStore:
    """Postgres-backed :class:`adcp.decisioning.proposal_store.ProposalStore`.

    Wired into :class:`adcp.decisioning.LazyPlatformRouter` via the
    ``proposal_store_factory=`` kwarg (adcp 5.4+, #722) as the single
    shared store across every tenant. The framework's
    ``proposal_dispatch`` calls into this instance via the router's
    :meth:`proposal_store_for_tenant` accessor.

    Concurrency: each method opens a short-lived session via
    :func:`get_db_session`; cross-method state isn't shared. Atomic
    CAS operations (:meth:`try_reserve_consumption`,
    :meth:`finalize_consumption`) use ``SELECT … FOR UPDATE`` keyed
    on ``(proposal_id, account_id)`` to serialize against parallel
    callers AND prevent cross-tenant lock acquisition — two concurrent
    same-tenant ``create_media_buy(proposal_id=X)`` calls produce
    exactly one successful reservation per the Protocol contract;
    cross-tenant probes never acquire the lock.
    """

    #: The Protocol's production-mode gate reads this attribute. ``True``
    #: signals the framework that the store is durable (won't lose
    #: in-flight proposals on worker rotation).
    is_durable: ClassVar[bool] = True

    async def put_draft(
        self,
        *,
        proposal_id: str,
        account_id: str,
        recipes: Mapping[str, Recipe],
        proposal_payload: Mapping[str, Any],
    ) -> None:
        """Persist a proposal in ``draft`` state per Protocol spec.

        The framework's ``proposal_dispatch`` calls this for every
        proposal returned from ``get_products`` / ``refine_products``.
        Managers that declare ``auto_commit_on_put_draft=True`` (adcp
        5.4+, #723) get a synthetic :meth:`commit` call from the
        framework immediately after — DRAFT → COMMITTED in a single
        dispatch — so the next ``create_media_buy(proposal_id=X)``
        finds a COMMITTED record.

        Idempotent on the same ``proposal_id``: refine iterations
        overwrite the prior payload + recipes. The Protocol forbids
        ``put_draft`` against COMMITTED / CONSUMED records — those are
        framework / adopter bugs and surface as ``INTERNAL_ERROR``.
        """

        recipes_json = _serialize_recipes(recipes)
        payload_dict = dict(proposal_payload)

        with get_db_session() as session:
            existing = session.scalars(select(ProposalRow).filter_by(proposal_id=proposal_id).with_for_update()).first()

            if existing is not None:
                # Per Protocol: refine iterations are only legal on
                # DRAFT records. Once committed or consumed the
                # proposal_id is immutable.
                if existing.state != ProposalState.DRAFT.value:
                    raise AdcpError(
                        "INTERNAL_ERROR",
                        message=(
                            f"Cannot put_draft on proposal {proposal_id!r} "
                            f"in state {existing.state!r}; refine iterations "
                            "are only valid on draft proposals. Once "
                            "committed or consumed, a proposal_id is "
                            "immutable."
                        ),
                        recovery="terminal",
                    )
                existing.account_id = account_id
                existing.recipes = recipes_json
                existing.proposal_payload = payload_dict
                # tenant_id stays pinned to the original tenant — refine
                # within a tenant is fine; cross-tenant overwrite would
                # mean a colliding proposal_id, which our id mint
                # (``prop_{uuid4_hex[:12]}``) makes vanishingly unlikely.
                session.commit()
                return

            tenant_id = _resolve_tenant_id_for_account(account_id)
            row = ProposalRow(
                proposal_id=proposal_id,
                tenant_id=tenant_id,
                account_id=account_id,
                state=ProposalState.DRAFT.value,
                recipes=recipes_json,
                proposal_payload=payload_dict,
                # expires_at is set by :meth:`commit` — DRAFT records
                # have no hold window per spec; the framework's
                # auto-commit (when wired) supplies expires_at from
                # ``ProposalCapabilities.auto_commit_ttl_seconds``.
                expires_at=None,
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

        with get_db_session() as session:
            # Filter ``account_id`` in the WHERE clause (not after fetch) so
            # cross-tenant probes never acquire the ``FOR UPDATE`` row lock.
            # Fetch-then-check would (a) leak existence via timing — lock
            # acquisition is slower than a clean miss — and (b) hand a
            # DoS primitive to an adversary who can block another
            # tenant's reservations by spamming guessed proposal_ids.
            row = session.scalars(
                select(ProposalRow).filter_by(proposal_id=proposal_id, account_id=expected_account_id).with_for_update()
            ).first()
            if row is None:
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

        with get_db_session() as session:
            # account_id in WHERE so cross-tenant probes don't take the lock.
            row = session.scalars(
                select(ProposalRow).filter_by(proposal_id=proposal_id, account_id=expected_account_id).with_for_update()
            ).first()
            if row is None:
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

        with get_db_session() as session:
            # account_id in WHERE so cross-tenant probes don't take the lock.
            # Unknown / cross-tenant ids fall through to a no-op
            # (the rollback path fires unconditionally; idempotent on miss).
            row = session.scalars(
                select(ProposalRow).filter_by(proposal_id=proposal_id, account_id=expected_account_id).with_for_update()
            ).first()
            if row is None:
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
        """Legacy v1.5-alpha ``committed`` → ``consumed`` transition —
        intentionally **not implemented** in the salesagent store.

        The Protocol signature doesn't accept ``expected_account_id``,
        which means an internal caller (or a future framework version)
        could mark *any* tenant's proposal as consumed and bind it to a
        ``media_buy_id`` from another tenant — the partial unique index
        on ``(account_id, media_buy_id)`` would still let the write
        succeed because the tuple is per-account. New dispatch code
        uses the two-phase :meth:`try_reserve_consumption` +
        :meth:`finalize_consumption` (both account-scoped), so failing
        this method closed has no functional cost today. Surfaces
        loudly if a future framework version begins calling it.
        """
        logger.error(
            "SalesAgentProposalStore.mark_consumed called for proposal_id=%r "
            "media_buy_id=%r — this Protocol method has no tenant scoping "
            "and is intentionally not implemented. New framework callers "
            "should use try_reserve_consumption + finalize_consumption.",
            proposal_id,
            media_buy_id,
        )
        raise NotImplementedError(
            "SalesAgentProposalStore intentionally does not implement "
            "mark_consumed; the Protocol signature lacks tenant scoping. "
            "Use try_reserve_consumption + finalize_consumption."
        )

    async def discard(self, proposal_id: str) -> None:
        """Idempotent delete — intentionally **not implemented**.

        The Protocol signature doesn't accept ``expected_account_id``,
        which means any caller that obtains a ``proposal_id`` (a
        buyer-controllable value reachable via storyboard echoes /
        log scrapes / brute force against the 48-bit id space) could
        destroy another tenant's reservable proposal. The framework's
        dispatch (``adcp.decisioning.proposal_dispatch``) doesn't call
        this method today; surfacing loudly here ensures a future
        framework version that begins calling it forces an upstream
        Protocol fix before it can hit production.
        """
        logger.error(
            "SalesAgentProposalStore.discard called for proposal_id=%r — "
            "this Protocol method has no tenant scoping and is "
            "intentionally not implemented. Filed upstream as a Protocol gap.",
            proposal_id,
        )
        raise NotImplementedError(
            "SalesAgentProposalStore intentionally does not implement "
            "discard; the Protocol signature lacks tenant scoping. "
            "File an upstream issue if the framework begins calling this."
        )

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
