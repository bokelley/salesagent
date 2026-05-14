"""merge audit_details repair with freewheel placement stats

PR #381 (freewheel placement stats — head 190d6e98754b) and PR #409
(audit_logs.details repair — head s1t2u3v4w5x6) both branched off
r0s1t2u3v4w5 and landed independently, leaving the chain with two heads.
Alembic refuses to upgrade past a divergent graph without an explicit
target, so the embedded-salesagent migrate job crashloops on rollout.

This is a pure graph-reconciliation revision — no schema or data changes.

Revision ID: d0c3c40fdd41
Revises: 190d6e98754b, s1t2u3v4w5x6
Create Date: 2026-05-14 03:34:04.518111

"""

from collections.abc import Sequence

revision: str = "d0c3c40fdd41"
down_revision: str | Sequence[str] | None = ("190d6e98754b", "s1t2u3v4w5x6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge branch heads — no schema changes."""


def downgrade() -> None:
    """Merge branch heads — no schema changes to revert."""
