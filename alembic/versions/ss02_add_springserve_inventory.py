"""add_springserve_inventory

Local cache of SpringServe inventory taxonomy (supply_partners, supply_tags,
and any taxonomy axes the operator's account exposes). Used by the
SpringServe adapter's product configuration UI so publishers can pick
targeting from synced inventory without round-tripping to the SpringServe
API on every page render.

NOT exposed to AdCP buyers -- buyer-facing property discovery goes through
the AAO lookup path (adagents.json + brand.json). This is a private
adapter-side cache.

Refreshed on demand via the adapter settings "Sync Inventory" button or
the periodic AdapterSyncScheduler.

Revision ID: ss02e5f6a7b8
Revises: ss01a1b2c3d4
Create Date: 2026-05-14 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ss02e5f6a7b8"
down_revision: str | Sequence[str] | None = "ss01a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create springserve_inventory table."""
    op.create_table(
        "springserve_inventory",
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column(
            "entity_type",
            sa.String(40),
            nullable=False,
            comment=("SpringServe entity kind: supply_partner, supply_tag, supply_group, account"),
        ),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(512), nullable=True),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column(
            "raw_json",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "entity_type", "entity_id"),
    )
    op.create_index(
        "idx_springserve_inventory_tenant_type",
        "springserve_inventory",
        ["tenant_id", "entity_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_springserve_inventory_tenant_type", table_name="springserve_inventory")
    op.drop_table("springserve_inventory")
