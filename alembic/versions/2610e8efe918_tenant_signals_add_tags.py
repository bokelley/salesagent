"""tenant_signals: add tags

Adds the AdCP ``Signal.tags`` column so operators can group / filter their
catalog (e.g. "premium", "sports", "high-cpm-target"). Defaults to empty
list. Lowercase + ``[a-z0-9_-]`` only — matches the AdCP spec's Tag pattern.

Revision ID: 2610e8efe918
Revises: y7z8a9b0c1d2
Create Date: 2026-05-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2610e8efe918"
down_revision: str | Sequence[str] | None = "y7z8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_signals",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_signals", "tags")
