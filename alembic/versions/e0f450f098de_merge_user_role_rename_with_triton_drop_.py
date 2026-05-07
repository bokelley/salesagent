"""merge user_role rename with triton-drop merge heads

Revision ID: e0f450f098de
Revises: 8407a32e9b07, p7q8r9s0t1u2
Create Date: 2026-05-07 13:14:22.703828

No-op merge migration to converge two independent heads that landed on
main in parallel:

- ``8407a32e9b07`` — rename ``User.role`` enum value ``manager`` → ``member``
  (PR #112 — sprint 4 RBAC, role enforcement on tenant-scoped admin routes).
- ``p7q8r9s0t1u2`` — earlier no-op merge that converged the
  Kevel/triton/house_domain drops from PRs #78, #110, #111.

Both branched from the same ancestors but neither is a child of the other,
so ``alembic upgrade head`` resolves to two heads. This empty merge
re-converges them. PR #135 attempted the same fix earlier but was opened
before #112 landed and is now itself stale on a single-head ancestor —
this PR supersedes that fix path.

The two changes don't overlap (enum rename on ``users`` vs column drops
on ``tenants``/``products``), so the merge is safe with empty
upgrade/downgrade — the canonical no-op merge shape.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

# revision identifiers, used by Alembic.
revision: str = "e0f450f098de"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "8407a32e9b07",
    "p7q8r9s0t1u2",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
