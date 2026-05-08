"""merge advertiser-assignment with phase1 slices

No-op merge migration. ``q9r0s1t2u3v4`` (advertiser→agent assignment +
external_id, from PR #136) and ``0fa8fa8610df`` (phase 1 slice 2 / fix-
duplication merge) both branched from ``523ed762edce`` and landed in
parallel. Re-converging here.

Revision ID: r1s2t3u4v5w6
Revises: 0fa8fa8610df, q9r0s1t2u3v4
Create Date: 2026-05-07

"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

revision: str = "r1s2t3u4v5w6"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "0fa8fa8610df",
    "q9r0s1t2u3v4",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
