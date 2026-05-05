"""add principal_id to advertiser_routing_rules

Revision ID: e7c8d9a0b1f2
Revises: d5e6f7a8b9c0
Create Date: 2026-05-04 22:00:00.000000

Sprint 5 workstream A0 — agent in the routing key.

Sprint 1.8 keyed ``advertiser_routing_rules`` on
``(tenant_id, operator_domain, brand_house, brand_id)``. This migration
adds ``principal_id`` to the natural key so standalone publishers can
route different agents to different GAM buckets.

Backward-compatible: existing rows get ``principal_id = NULL`` (matches
any agent — preserves Sprint 1.8 behavior).

The natural-key uniqueness index uses ``COALESCE(principal_id, '')``
just like ``brand_house`` and ``brand_id`` so NULL participates in
uniqueness — two "any-agent" rules under the same operator+brand
collide.

See ``docs/design/embedded-mode-sprint-5-buyer-routing-ux.md``
"Schema extension: agent in the routing key".
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7c8d9a0b1f2"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "advertiser_routing_rules",
        sa.Column("principal_id", sa.String(length=50), nullable=True),
    )

    # Drop the Sprint 1.8 COALESCE-unique index, recreate with
    # principal_id added as a (NULL-tolerant) component.
    op.drop_index("uq_routing_rule_natural_key", table_name="advertiser_routing_rules")
    op.create_index(
        "uq_routing_rule_natural_key",
        "advertiser_routing_rules",
        [
            "tenant_id",
            sa.text("COALESCE(principal_id, '')"),
            "operator_domain",
            sa.text("COALESCE(brand_house, '')"),
            sa.text("COALESCE(brand_id, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_routing_rule_natural_key", table_name="advertiser_routing_rules")
    op.create_index(
        "uq_routing_rule_natural_key",
        "advertiser_routing_rules",
        [
            "tenant_id",
            "operator_domain",
            sa.text("COALESCE(brand_house, '')"),
            sa.text("COALESCE(brand_id, '')"),
        ],
        unique=True,
    )
    op.drop_column("advertiser_routing_rules", "principal_id")
