"""``SalesAgentProposalStore`` — Postgres-backed ``ProposalStore`` impl.

Persists the AdCP v1.5 proposal lifecycle (DRAFT → COMMITTED → CONSUMING
→ CONSUMED) so ``create_media_buy(proposal_id=X)`` can hydrate the
proposal across replicas. The upstream ``InMemoryProposalStore`` is a
process-local dict; with ``ADCP_STATELESS_HTTP=true`` (PR #376) every
request lands on a random replica and the proposal evaporates.

Wired into ``LazyPlatformRouter`` via the ``proposal_store_factory=``
kwarg in ``core/main.py`` — the factory returns one instance per
tenant id. Each instance scopes every query by ``self._tenant_id``;
cross-tenant probes return None / raise PROPOSAL_NOT_FOUND.

The Protocol's nine methods map to focused SQL:

* ``put_draft`` — UPSERT into ``proposal_drafts`` (overwrite is legal
  while DRAFT; rejected when state is COMMITTED/CONSUMING/CONSUMED).
* ``get`` — point lookup with optional ``expected_account_id`` filter.
* ``commit`` — UPDATE … WHERE state='DRAFT' → COMMITTED + expires_at.
  Idempotent on COMMITTED if (expires_at, payload) match exactly.
* ``try_reserve_consumption`` — atomic CAS via ``UPDATE … WHERE
  state='COMMITTED' RETURNING *``. Two parallel callers can't both
  reserve (Postgres serializes the UPDATE within the row lock).
* ``finalize_consumption`` — UPDATE state='CONSUMING' → CONSUMED +
  set media_buy_id.
* ``release_consumption`` — UPDATE state='CONSUMING' → COMMITTED.
* ``mark_consumed`` — legacy direct COMMITTED → CONSUMED (v1.5 alpha).
* ``discard`` — DELETE; idempotent on missing.
* ``get_by_media_buy_id`` — reverse-index lookup (partial unique on
  ``(tenant_id, media_buy_id) WHERE media_buy_id IS NOT NULL``).

All methods are sync; the framework awaits them via ``_await_maybe``
(``MaybeAsync`` contract — both shapes are valid).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

from adcp.decisioning import AdcpError
from adcp.decisioning.proposal_store import ProposalRecord, ProposalState
from adcp.decisioning.recipe import Recipe
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import ProposalDraft


def _record_from_row(row: ProposalDraft) -> ProposalRecord:
    """Hydrate a ``ProposalRecord`` from an ORM row.

    Recipes are stored as a dict at the column level; rehydrate each
    value into a ``Recipe`` Pydantic instance so the framework's
    ``ctx.recipes`` typed surface holds. A future ``recipe_schema_version``
    bump can read the value off the row before rehydration if we add a
    column for it; today we pin to 1 (the v1.5 default).
    """
    recipes_raw = row.recipes or {}
    recipes: dict[str, Recipe] = {pid: Recipe.model_validate(payload) for pid, payload in recipes_raw.items()}
    return ProposalRecord(
        proposal_id=row.proposal_id,
        account_id=row.account_id,
        state=ProposalState(row.state.lower()),
        recipes=recipes,
        proposal_payload=dict(row.proposal_payload or {}),
        expires_at=row.expires_at,
        media_buy_id=row.media_buy_id,
        recipe_schema_version=1,
    )


def _serialize_recipes(recipes: Mapping[str, Recipe]) -> dict[str, dict[str, Any]]:
    """Reverse of :func:`_record_from_row`'s recipe rehydration."""
    out: dict[str, dict[str, Any]] = {}
    for pid, recipe in recipes.items():
        out[pid] = recipe.model_dump(mode="json", exclude_none=True)
    return out


class SalesAgentProposalStore:
    """Postgres-backed per-tenant ``ProposalStore``.

    Mirrors :class:`adcp.decisioning.InMemoryProposalStore`'s shape
    against the salesagent ``proposal_drafts`` table. Tenant scoping
    is via the ``tenant_id`` field bound at construction; every query
    composes ``(tenant_id, proposal_id)`` so cross-tenant probes
    return None / PROPOSAL_NOT_FOUND.

    :param tenant_id: The tenant this store instance serves. Built
        per-request via ``core.main._proposal_store_for_tenant``; same
        tenant_id reuses the same instance (LRU-cached at the factory).
    """

    # ``is_durable`` drives the framework's production-mode gate —
    # ``InMemoryProposalStore`` is False; Postgres-backed is True so
    # the framework's commit-finalize storyboard recognizes us as
    # production-grade. Per :class:`ProposalStore` Protocol.
    is_durable: ClassVar[bool] = True

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id

    # ──────────────────────────────────────────────────────────────────
    # Protocol method implementations
    # ──────────────────────────────────────────────────────────────────

    def put_draft(
        self,
        *,
        proposal_id: str,
        account_id: str,
        recipes: Mapping[str, Recipe],
        proposal_payload: Mapping[str, Any],
    ) -> None:
        """Insert a fresh DRAFT or overwrite an existing DRAFT row.

        Calling on a COMMITTED / CONSUMING / CONSUMED row raises
        ``INTERNAL_ERROR`` — adopter bug (framework wouldn't dispatch
        put_draft after commit).
        """
        recipes_json = _serialize_recipes(recipes)
        payload_json = dict(proposal_payload)
        with get_db_session() as session:
            existing = self._lookup(session, proposal_id)
            if existing is None:
                row = ProposalDraft(
                    tenant_id=self._tenant_id,
                    proposal_id=proposal_id,
                    account_id=account_id,
                    state=ProposalState.DRAFT.value.upper(),
                    proposal_payload=payload_json,
                    recipes=recipes_json,
                    media_buy_id=None,
                    expires_at=None,
                )
                session.add(row)
            else:
                if existing.state.lower() != ProposalState.DRAFT.value:
                    raise AdcpError(
                        "INTERNAL_ERROR",
                        message=(
                            f"put_draft called on proposal {proposal_id!r} in state "
                            f"{existing.state!r} — only DRAFT records can be overwritten. "
                            "This is a framework / adopter bug; refine iterations write "
                            "while DRAFT, post-commit changes go through commit/refine "
                            "lifecycle, not put_draft."
                        ),
                        field="proposal_id",
                    )
                existing.account_id = account_id
                existing.proposal_payload = payload_json
                existing.recipes = recipes_json
            session.commit()

    def get(
        self,
        proposal_id: str,
        *,
        expected_account_id: str | None = None,
    ) -> ProposalRecord | None:
        """Point lookup. Account mismatch returns None (not the raw record)."""
        with get_db_session() as session:
            row = self._lookup(session, proposal_id)
            if row is None:
                return None
            if expected_account_id is not None and row.account_id != expected_account_id:
                return None
            return _record_from_row(row)

    def commit(
        self,
        proposal_id: str,
        *,
        expires_at: datetime,
        proposal_payload: Mapping[str, Any],
    ) -> None:
        """Promote DRAFT → COMMITTED. Idempotent on COMMITTED with matching
        (expires_at, payload); raises INTERNAL_ERROR on mismatch.
        """
        payload_json = dict(proposal_payload)
        with get_db_session() as session:
            row = self._lookup(session, proposal_id)
            if row is None:
                raise AdcpError(
                    "PROPOSAL_NOT_FOUND",
                    message=f"commit on unknown proposal {proposal_id!r}",
                    field="proposal_id",
                )
            current_state = row.state.lower()
            if current_state == ProposalState.COMMITTED.value:
                # Idempotency check on the second commit.
                if row.expires_at != expires_at or dict(row.proposal_payload or {}) != payload_json:
                    raise AdcpError(
                        "INTERNAL_ERROR",
                        message=(
                            f"commit re-call on proposal {proposal_id!r} with different "
                            "(expires_at, proposal_payload) values — framework / adopter bug."
                        ),
                        field="proposal_id",
                    )
                return
            if current_state != ProposalState.DRAFT.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"commit on proposal {proposal_id!r} in state {row.state!r} — "
                        "only DRAFT records are commit-promotable."
                    ),
                    field="proposal_id",
                )
            row.state = ProposalState.COMMITTED.value.upper()
            row.expires_at = expires_at
            row.proposal_payload = payload_json
            session.commit()

    def try_reserve_consumption(
        self,
        proposal_id: str,
        *,
        expected_account_id: str,
    ) -> ProposalRecord:
        """Atomic CAS COMMITTED → CONSUMING. Postgres row lock prevents
        the parallel-reserve race documented in the Protocol.
        """
        with get_db_session() as session:
            # ``SELECT … FOR UPDATE`` holds the row lock until commit/rollback.
            row = session.scalars(
                select(ProposalDraft).filter_by(tenant_id=self._tenant_id, proposal_id=proposal_id).with_for_update()
            ).first()
            if row is None:
                raise AdcpError(
                    "PROPOSAL_NOT_FOUND",
                    message=f"try_reserve_consumption on unknown proposal {proposal_id!r}",
                    field="proposal_id",
                )
            if row.account_id != expected_account_id:
                # Cross-account probe: surface as NOT_FOUND so a buyer
                # can't enumerate proposal_ids across accounts.
                raise AdcpError(
                    "PROPOSAL_NOT_FOUND",
                    message=f"try_reserve_consumption on unknown proposal {proposal_id!r}",
                    field="proposal_id",
                )
            current_state = row.state.lower()
            if current_state != ProposalState.COMMITTED.value:
                raise AdcpError(
                    "PROPOSAL_NOT_COMMITTED",
                    message=(
                        f"try_reserve_consumption on proposal {proposal_id!r} in state "
                        f"{row.state!r} — only COMMITTED records can be reserved. "
                        "A parallel reserve already won the race, or the proposal "
                        "hasn't been committed yet."
                    ),
                    field="proposal_id",
                )
            row.state = ProposalState.CONSUMING.value.upper()
            session.commit()
            return _record_from_row(row)

    def finalize_consumption(
        self,
        proposal_id: str,
        *,
        media_buy_id: str,
        expected_account_id: str,
    ) -> None:
        """Promote CONSUMING → CONSUMED and stamp media_buy_id."""
        with get_db_session() as session:
            row = self._lookup(session, proposal_id, expected_account_id=expected_account_id)
            if row is None:
                # The framework only finalizes a previously-reserved row.
                # Missing here means a bug (framework called finalize
                # without a prior reserve), not a buyer-visible error.
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"finalize_consumption on unknown / cross-account proposal "
                        f"{proposal_id!r}. The framework only calls this after a "
                        "successful try_reserve_consumption — this is a "
                        "framework / adopter bug."
                    ),
                    field="proposal_id",
                )
            if row.state.lower() != ProposalState.CONSUMING.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"finalize_consumption on proposal {proposal_id!r} in state "
                        f"{row.state!r} — only CONSUMING records can be finalized."
                    ),
                    field="proposal_id",
                )
            row.state = ProposalState.CONSUMED.value.upper()
            row.media_buy_id = media_buy_id
            session.commit()

    def release_consumption(
        self,
        proposal_id: str,
        *,
        expected_account_id: str,
    ) -> None:
        """Rollback CONSUMING → COMMITTED. Idempotent on COMMITTED."""
        with get_db_session() as session:
            row = self._lookup(session, proposal_id, expected_account_id=expected_account_id)
            if row is None:
                # Same INTERNAL_ERROR rationale as finalize.
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"release_consumption on unknown / cross-account proposal "
                        f"{proposal_id!r}. Called outside the reserve / finalize "
                        "/ release cycle — framework / adopter bug."
                    ),
                    field="proposal_id",
                )
            current_state = row.state.lower()
            if current_state == ProposalState.COMMITTED.value:
                # Already-rolled-back idempotency.
                return
            if current_state != ProposalState.CONSUMING.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"release_consumption on proposal {proposal_id!r} in state "
                        f"{row.state!r} — only CONSUMING (rollback) or COMMITTED "
                        "(idempotent no-op) records are valid here."
                    ),
                    field="proposal_id",
                )
            row.state = ProposalState.COMMITTED.value.upper()
            session.commit()

    def mark_consumed(
        self,
        proposal_id: str,
        *,
        media_buy_id: str,
    ) -> None:
        """Legacy direct COMMITTED → CONSUMED (v1.5 alpha). Equivalent to
        reserve-and-finalize against a single thread of writes. New code
        uses the two-phase commit path.
        """
        with get_db_session() as session:
            row = self._lookup(session, proposal_id)
            if row is None:
                raise AdcpError(
                    "PROPOSAL_NOT_FOUND",
                    message=f"mark_consumed on unknown proposal {proposal_id!r}",
                    field="proposal_id",
                )
            current_state = row.state.lower()
            if current_state == ProposalState.CONSUMED.value:
                # Idempotent — same media_buy_id check guards against
                # callers double-marking under different terminal ids.
                if row.media_buy_id != media_buy_id:
                    raise AdcpError(
                        "INTERNAL_ERROR",
                        message=(
                            f"mark_consumed on proposal {proposal_id!r} already CONSUMED "
                            f"with media_buy_id={row.media_buy_id!r}; got {media_buy_id!r}."
                        ),
                        field="proposal_id",
                    )
                return
            if current_state != ProposalState.COMMITTED.value:
                raise AdcpError(
                    "INTERNAL_ERROR",
                    message=(
                        f"mark_consumed on proposal {proposal_id!r} in state {row.state!r} "
                        "— only COMMITTED records support the legacy direct transition."
                    ),
                    field="proposal_id",
                )
            row.state = ProposalState.CONSUMED.value.upper()
            row.media_buy_id = media_buy_id
            session.commit()

    def discard(self, proposal_id: str) -> None:
        """DELETE the row. Idempotent on missing — no raise."""
        with get_db_session() as session:
            row = self._lookup(session, proposal_id)
            if row is None:
                return
            session.delete(row)
            session.commit()

    def get_by_media_buy_id(
        self,
        media_buy_id: str,
        *,
        expected_account_id: str,
    ) -> ProposalRecord | None:
        """Reverse-index lookup via the partial unique on
        ``(tenant_id, media_buy_id) WHERE media_buy_id IS NOT NULL``.
        """
        with get_db_session() as session:
            row = session.scalars(
                select(ProposalDraft).filter_by(
                    tenant_id=self._tenant_id,
                    media_buy_id=media_buy_id,
                    account_id=expected_account_id,
                )
            ).first()
            if row is None:
                return None
            return _record_from_row(row)

    # ──────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────

    def _lookup(
        self,
        session: Session,
        proposal_id: str,
        *,
        expected_account_id: str | None = None,
    ) -> ProposalDraft | None:
        """Fetch by ``(tenant_id, proposal_id)`` and optionally
        ``account_id``. Returns None on cross-tenant or cross-account
        probes — never raises so callers can distinguish "missing" from
        "wrong state" at their own granularity.
        """
        stmt = select(ProposalDraft).filter_by(tenant_id=self._tenant_id, proposal_id=proposal_id)
        if expected_account_id is not None:
            stmt = stmt.filter_by(account_id=expected_account_id)
        return session.scalars(stmt).first()
