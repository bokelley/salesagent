"""Expand sync_jobs.sync_type for guidance sync kinds.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column(
        "sync_jobs",
        "sync_type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    too_long = conn.execute(
        sa.text("select sync_type from sync_jobs where length(sync_type) > 20 limit 1")
    ).scalar_one_or_none()
    if too_long is not None:
        raise RuntimeError(
            "Cannot downgrade sync_jobs.sync_type to varchar(20) while rows contain "
            f"sync_type values longer than 20 characters, e.g. {too_long!r}."
        )
    op.alter_column(
        "sync_jobs",
        "sync_type",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
