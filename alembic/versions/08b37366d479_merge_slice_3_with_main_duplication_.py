"""merge slice 3 with main duplication-ratchet

Revision ID: 08b37366d479
Revises: d1b53e88b3a6, 0fa8fa8610df
Create Date: 2026-05-07 18:14:08.442527

No-op merge migration converging slice 3's signing_mode head with main's
post-PR-#140 (duplication ratchet rebuild) merge head. Both ancestors are
schema-stable; the divergence is purely the ordering of multiple parallel
fix/test PRs landing on main while slice 3 was in review. Empty
upgrade/downgrade — the canonical no-op merge shape.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

revision: str = "08b37366d479"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "d1b53e88b3a6",
    "0fa8fa8610df",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
