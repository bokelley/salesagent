"""merge slice 3 signing_mode with phase 1 slice 2

Revision ID: d1b53e88b3a6
Revises: 789f9d88265e, 523ed762edce
Create Date: 2026-05-07 14:56:53.375995

No-op merge migration converging two parallel heads:

* ``789f9d88265e`` — slice 3 ``push_notification_configs.signing_mode`` column
* ``523ed762edce`` — main's phase-1-slice-2 merge head (drops ``Product.model_dump``
  override and accumulated unrelated migrations)

The two changes touch disjoint tables (``push_notification_configs`` vs
``products``/``tenants``), so the merge is safe with empty upgrade/downgrade —
the canonical no-op merge shape.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

revision: str = "d1b53e88b3a6"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "789f9d88265e",
    "523ed762edce",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
