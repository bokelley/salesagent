"""add_springserve_demand_tag_stats

Per-demand-tag delivery stats cache for the SpringServe adapter. Populated
by a periodic Reporting API sync (Stage 4; the sync writer is wired but
the SpringServe Reporting scope grant lands separately). Read by
``SpringServeAdapter.get_packages_snapshot`` and
``SpringServeAdapter.get_media_buy_delivery`` so AdCP delivery surfaces
can serve results without round-tripping to SpringServe on every poll.

Stays empty until the reporting sync is wired up and scope is granted.
Adapter reads are defensive -- missing rows surface as ``DeliveryDataUnavailable``
rather than fake-zero delivery, matching the FreeWheel adapter contract.

Spend is stored as currency-minor-unit micros (1 EUR = 1_000_000 micros)
to avoid floating-point precision loss when aggregating.

Revision ID: ss01a1b2c3d4
Revises: d0c3c40fdd41
Create Date: 2026-05-14 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ss01a1b2c3d4"
down_revision: str | Sequence[str] | None = "d0c3c40fdd41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create springserve_demand_tag_stats table."""
    op.create_table(
        "springserve_demand_tag_stats",
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column(
            "demand_tag_id",
            sa.String(64),
            nullable=False,
            comment="SpringServe-assigned demand_tag identifier (the per-package delivery unit).",
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            nullable=True,
            comment="SpringServe campaign this demand_tag belongs to (denormalised for campaign-scoped queries).",
        ),
        sa.Column(
            "impressions",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="Total impressions delivered against this demand_tag (cumulative).",
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
            comment="Total spend in currency-minor-unit micros (1 EUR = 1_000_000). Avoids floating-point precision loss.",
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
            comment="Latest SpringServe-reported delivery state (delivering, completed, paused, ...).",
        ),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp SpringServe reported these metrics as of (data freshness boundary).",
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When this row was last refreshed by the reporting sync job.",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "demand_tag_id"),
    )
    op.create_index(
        "idx_ss_demand_tag_stats_tenant_campaign",
        "springserve_demand_tag_stats",
        ["tenant_id", "campaign_id"],
    )


def downgrade() -> None:
    """Drop springserve_demand_tag_stats table."""
    op.drop_index(
        "idx_ss_demand_tag_stats_tenant_campaign",
        table_name="springserve_demand_tag_stats",
    )
    op.drop_table("springserve_demand_tag_stats")
