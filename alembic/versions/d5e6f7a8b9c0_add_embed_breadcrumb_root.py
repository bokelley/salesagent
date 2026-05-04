"""add embed_breadcrumb_root to tenants

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-04 20:00:00.000000

Add ``tenants.embed_breadcrumb_root`` (JSONB, nullable). When the tenant
is rendered inside an upstream host (``is_embedded=true``), this column
configures the first crumb of the breadcrumb trail — typically the host's
storefront homepage. Shape: ``{"label": str, "url": str}``.

Only meaningful when ``is_embedded`` is true; open-instance tenants ignore
the value even if set.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tenants",
        sa.Column(
            "embed_breadcrumb_root",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenants", "embed_breadcrumb_root")
