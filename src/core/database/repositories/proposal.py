"""Cross-tenant maintenance helpers for the AdCP proposal store table."""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

EXPIRED_PROPOSAL_STATES = ("draft", "committed")

_EXPIRED_PROPOSALS_WHERE = """
    expires_at < now()
    AND state IN :states
"""

_COUNT_EXPIRED_PROPOSALS = text(
    f"""
    SELECT count(*)
    FROM proposals
    WHERE {_EXPIRED_PROPOSALS_WHERE}
    """
).bindparams(bindparam("states", expanding=True))

_DELETE_EXPIRED_PROPOSALS = text(
    f"""
    WITH doomed AS (
        SELECT ctid
        FROM proposals
        WHERE {_EXPIRED_PROPOSALS_WHERE}
        ORDER BY expires_at
        LIMIT :batch_size
        FOR UPDATE SKIP LOCKED
    )
    DELETE FROM proposals AS p
    USING doomed
    WHERE p.ctid = doomed.ctid
    """
).bindparams(bindparam("states", expanding=True), bindparam("batch_size"))


class ProposalMaintenanceRepository:
    """Operational maintenance for proposal-store rows.

    The live proposal protocol owns regular reads and writes through
    ``core.decisioning.proposal_store``. These methods are only for global
    retention cleanup and deliberately use SQL against the table shape shared
    with the upstream PgProposalStore.
    """

    @staticmethod
    def count_expired_unconsumed(session: Session) -> int:
        """Count expired draft/committed proposal rows eligible for cleanup."""
        return int(session.execute(_COUNT_EXPIRED_PROPOSALS, {"states": EXPIRED_PROPOSAL_STATES}).scalar_one())

    @staticmethod
    def delete_expired_unconsumed(session: Session, *, batch_size: int) -> int:
        """Delete expired draft/committed proposals and return the rowcount.

        Consumed rows are preserved as the audit trail for successfully created
        media buys; in-flight ``consuming`` rows are also left untouched.
        Caller commits.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        result = session.execute(
            _DELETE_EXPIRED_PROPOSALS,
            {"states": EXPIRED_PROPOSAL_STATES, "batch_size": batch_size},
        )
        rowcount = getattr(result, "rowcount", 0) or 0
        return int(rowcount)
