"""Add inventory profile forecast and pricing analytics.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-05-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "h8i9j0k1l2m3"
down_revision: str | None = "g7h8i9j0k1l2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("inventory_profiles", sa.Column("forecast", postgresql.JSONB(), nullable=True))
    op.add_column(
        "inventory_profiles",
        sa.Column("pricing_availability", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inventory_profiles", "pricing_availability")
    op.drop_column("inventory_profiles", "forecast")
