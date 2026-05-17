"""embedded composition foundation

Additive schema for the embedded composition API
(`docs/design/...` / `.context/embedded-composition-design.md`):

- ``inventory_profiles.constraints`` — typed AdCP capability narrowings
  (formats, channels, targeting_dimensions) so storefronts can pre-validate
  compositions client-side.
- ``inventory_profiles.etag`` — content hash for cache invalidation.
- ``principals.external_id`` — storefront-supplied stable id for idempotent
  principal creation through the new REST surface.
- ``tenants.max_composition_ttl_seconds`` — operator-configurable upper bound
  on dynamic product validity (default 7 days).
- ``custom_targeting_profiles`` — net-new table. Composable overlay for
  targeting that AdCP cannot express natively (operator-specific custom
  key-values, adapter-opaque audience segment ids).

Purely additive — no rename, no drop, no behavior change for existing rows.

Revision ID: z8a9b0c1d2e3
Revises: t2u3v4w5x6y7
Create Date: 2026-05-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "z8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "t2u3v4w5x6y7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inventory_profiles",
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "inventory_profiles",
        sa.Column("etag", sa.String(64), nullable=True),
    )

    op.add_column(
        "principals",
        sa.Column("external_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_principals_external_id",
        "principals",
        ["tenant_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.add_column(
        "tenants",
        sa.Column(
            "max_composition_ttl_seconds",
            sa.Integer,
            nullable=False,
            server_default=sa.text("604800"),
        ),
    )

    op.create_table(
        "custom_targeting_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(50),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("custom_targeting_profile_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "adapter_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "touches_dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("etag", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "custom_targeting_profile_id",
            name="uq_custom_targeting_profile",
        ),
    )
    op.create_index(
        "idx_custom_targeting_profiles_tenant",
        "custom_targeting_profiles",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_custom_targeting_profiles_tenant",
        table_name="custom_targeting_profiles",
    )
    op.drop_table("custom_targeting_profiles")

    op.drop_column("tenants", "max_composition_ttl_seconds")

    op.drop_index("idx_principals_external_id", table_name="principals")
    op.drop_column("principals", "external_id")

    op.drop_column("inventory_profiles", "etag")
    op.drop_column("inventory_profiles", "constraints")
