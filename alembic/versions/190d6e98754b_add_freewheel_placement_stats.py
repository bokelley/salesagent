"""add_freewheel_placement_stats

Per-placement delivery stats cache. Populated by a periodic Query Reporting
API sync (pending FreeWheel scope grant — see docs/adapters/freewheel/README.md).
Read by ``FreeWheelAdapter.get_packages_snapshot`` and
``FreeWheelAdapter.get_media_buy_delivery`` so those AdCP surfaces can serve
results without round-tripping to FW on every request.

Stays empty until the Reporting API client is wired up. Adapter code reads
defensively — missing rows surface as ``None`` snapshots / zero delivery,
not errors.

Revision ID: 190d6e98754b
Revises: 7c3073bd70cf, r0s1t2u3v4w5
Create Date: 2026-05-12 21:01:41.886673
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "190d6e98754b"
# Merge revision: joins the FW inventory cache lineage (7c3073bd70cf) and the
# main lineage. main's head moves forward as new migrations land — this
# revision's parent on the main side gets re-pointed at each origin/main
# merge so the graph always converges to a single head. Today: r0s1t2u3v4w5
# (proposals table), which itself descends through 8820c87e8ae3 →
# 17423a1b551e back to base.
down_revision: str | Sequence[str] | None = ("7c3073bd70cf", "r0s1t2u3v4w5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create freewheel_placement_stats table."""
    op.create_table(
        "freewheel_placement_stats",
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column(
            "placement_id",
            sa.String(64),
            nullable=False,
            comment="FW-assigned placement identifier (matches FreeWheelInventory.entity_id for ad_unit_packages, or commercial Placement.id).",
        ),
        sa.Column(
            "insertion_order_id",
            sa.String(64),
            nullable=True,
            comment="FW insertion order this placement belongs to (denormalised for IO-scoped queries).",
        ),
        sa.Column(
            "impressions",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="Total impressions delivered against this placement (cumulative).",
        ),
        sa.Column(
            "completed_views",
            sa.BigInteger(),
            nullable=True,
            comment="Video/audio completions (for VAST inventory).",
        ),
        sa.Column(
            "clicks",
            sa.BigInteger(),
            nullable=True,
            comment="Total clicks (when reported).",
        ),
        sa.Column(
            "spend_micros",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="Total spend in currency-minor-unit micros (1 USD = 1_000_000 micros). Avoids floating-point precision loss.",
        ),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=True,
            comment="ISO 4217 currency for spend_micros.",
        ),
        sa.Column(
            "delivery_status",
            sa.String(40),
            nullable=True,
            comment="Latest FW-reported delivery state (delivering, completed, paused, …).",
        ),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp FW reported these metrics as of (data freshness boundary).",
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When this row was last refreshed by the reporting sync job.",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "placement_id"),
    )
    op.create_index(
        "idx_fw_placement_stats_tenant_io",
        "freewheel_placement_stats",
        ["tenant_id", "insertion_order_id"],
    )


def downgrade() -> None:
    """Drop freewheel_placement_stats table."""
    op.drop_index("idx_fw_placement_stats_tenant_io", table_name="freewheel_placement_stats")
    op.drop_table("freewheel_placement_stats")
