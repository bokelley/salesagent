"""merge mock-platform branch into all current heads

Reconciles three open heads as of 2026-05-07 evening:
- ``789f9d88265e`` (main tip: add push_notification_configs.signing_mode)
- ``523ed762edce`` (sibling main tip: phase1 slice 2 + latest main)
- ``o6p7q8r9s0t1`` (drop tenants.house_domain — line my branch was
  based on before main re-merged its tip)

The two main-side heads sit on parallel descents from
``ee6fe59f5407`` that never converge upstream of either, so this
migration pulls them all together into a single graph head.

Revision ID: d8e9f0a1b2c3
Revises: 789f9d88265e, 523ed762edce, o6p7q8r9s0t1
Create Date: 2026-05-07 15:55:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = ("789f9d88265e", "523ed762edce", "o6p7q8r9s0t1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration — no schema changes."""
    pass


def downgrade() -> None:
    """Merge migration — no schema changes to revert."""
    pass
