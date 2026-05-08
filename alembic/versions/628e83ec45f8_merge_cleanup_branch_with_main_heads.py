"""merge cleanup branch with main heads

Revision ID: 628e83ec45f8
Revises: 51a885014fac, f81308a72e28
Create Date: 2026-05-08 07:25:09.346956

No-op merge migration converging two parallel main heads brought in by
this branch's merge from origin/main. Both ancestors are schema-stable;
the divergence is purely the ordering of parallel PRs landing on main.
Empty upgrade/downgrade — the canonical no-op merge shape.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

revision: str = "628e83ec45f8"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "51a885014fac",
    "f81308a72e28",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
