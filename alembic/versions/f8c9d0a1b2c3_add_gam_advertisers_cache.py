"""add gam_advertisers cache

Revision ID: f8c9d0a1b2c3
Revises: e7c8d9a0b1f2
Create Date: 2026-05-04 22:30:00.000000

Sprint 5 workstream A — GAM advertisers cache.

The Buyer Routing UI needs a searchable list of the publisher's GAM
advertisers (10k+ advertisers per network is realistic). Round-tripping
to GAM on every keystroke is too expensive; we cache the
``CompanyService.getCompaniesByStatement WHERE type = 'ADVERTISER'``
result locally and serve the picker out of the cache.

Composite primary key on ``(tenant_id, advertiser_id)`` — the same
advertiser id can exist across publishers but each publisher's cache is
isolated.

Soft-delete: advertisers that disappear from GAM are flagged
``status='inactive'`` rather than hard-deleted because routing rules
might still reference them. The Buyer Routing UI surfaces inactive
status as a warning; the picker hides them by default.

See ``docs/design/embedded-mode-sprint-5-buyer-routing-ux.md`` "Piece D".
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8c9d0a1b2c3"
down_revision: str | Sequence[str] | None = "e7c8d9a0b1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gam_advertisers",
        sa.Column(
            "tenant_id",
            sa.String(length=50),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("advertiser_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_gam_advertisers_tenant",
        "gam_advertisers",
        ["tenant_id"],
    )
    # Compound index supports the case-insensitive name search in
    # GET /gam/advertisers — Postgres can satisfy ``WHERE tenant_id = ?
    # AND lower(name) LIKE ?`` from this index when the planner picks it.
    op.create_index(
        "idx_gam_advertisers_name",
        "gam_advertisers",
        ["tenant_id", "name"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_gam_advertisers_name", table_name="gam_advertisers")
    op.drop_index("idx_gam_advertisers_tenant", table_name="gam_advertisers")
    op.drop_table("gam_advertisers")
