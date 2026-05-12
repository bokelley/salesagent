"""add_freewheel_inventory_cache

Internal cache of FreeWheel inventory taxonomy used by the FreeWheel adapter's
product setup UI. Stores Sites, SiteSections, SiteGroups, Series, VideoGroups,
AdUnitPackages, AdUnitNodes, and StandardAttributes as JSON-blob rows keyed by
``(tenant_id, entity_type, entity_id)``.

This table is NOT exposed to AdCP buyers — buyer-facing property discovery
goes through the AAO lookup path (adagents.json + brand.json). The cache
exists purely so the publisher's product configuration UI can pick targeting
from FW inventory without round-tripping to the FW API on every page render.

Revision ID: 7c3073bd70cf
Revises: 17423a1b551e
Create Date: 2026-05-12 17:41:23.045835
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3073bd70cf"
down_revision: str | Sequence[str] | None = "17423a1b551e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create freewheel_inventory table."""
    op.create_table(
        "freewheel_inventory",
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column(
            "entity_type",
            sa.String(40),
            nullable=False,
            comment=(
                "FW entity kind: site, site_section, site_group, series, "
                "video_group, ad_unit_package, ad_unit_node, standard_attribute"
            ),
        ),
        sa.Column(
            "entity_id",
            sa.String(64),
            nullable=False,
            comment="FreeWheel-assigned identifier for this entity",
        ),
        sa.Column(
            "name",
            sa.String(512),
            nullable=True,
            comment="Human-readable name (denormalised for fast listing/search)",
        ),
        sa.Column(
            "parent_id",
            sa.String(64),
            nullable=True,
            comment=(
                "Optional FW parent id (e.g. site_section.parent = site, "
                "ad_unit_node.parent = placement). Used for hierarchical UI."
            ),
        ),
        sa.Column(
            "raw_json",
            JSONB,
            nullable=False,
            comment="Full FW response payload — preserves fields we don't denormalise.",
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When this row was last refreshed from the FW API.",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "entity_type", "entity_id"),
    )
    op.create_index(
        "idx_freewheel_inventory_tenant_type",
        "freewheel_inventory",
        ["tenant_id", "entity_type"],
    )
    op.create_index(
        "idx_freewheel_inventory_parent",
        "freewheel_inventory",
        ["tenant_id", "parent_id"],
    )


def downgrade() -> None:
    """Drop freewheel_inventory table."""
    op.drop_index("idx_freewheel_inventory_parent", table_name="freewheel_inventory")
    op.drop_index("idx_freewheel_inventory_tenant_type", table_name="freewheel_inventory")
    op.drop_table("freewheel_inventory")
