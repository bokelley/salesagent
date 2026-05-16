"""drop custom_targeting_profiles; add tenant_signals

Architectural pivot for the embedded composition API: replace the parallel
``CustomTargetingProfile`` entity (with hardcoded ``key_values`` /
``audience_segments`` components — GAM-shaped) with ``TenantSignal``, an
operator-authored map of adapter capabilities shaped after AdCP's existing
``Signal`` type.

Why: the storefront needs to render UI for adapter-specific targeting
(weather.com temperature ranges, sports.com team taxonomies, custom KVs,
audiences, …) without per-adapter branching. AdCP ``Signal`` already
carries the self-describing schema (value_type ∈ {binary, categorical,
numeric}, ``range``, ``categories``) the storefront needs to render UI.
The operator declares one TenantSignal per capability they expose;
adapter-specific resolution lives in ``adapter_config`` (opaque to the
storefront, consumed by the per-adapter materializer at compose time).

Composition of signals (a + b - c) lives in the existing custom-targeting
runtime resolution — no separate "signal bundle" entity needed.

This migration also drops ``products.custom_targeting_profile_ids``
(replaced at compose-time by snapshotting selections into
``Product.implementation_config``).

Revision ID: w5x6y7z8a9b0
Revises: v4w5x6y7z8a9
Create Date: 2026-05-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w5x6y7z8a9b0"
down_revision: str | Sequence[str] | None = "v4w5x6y7z8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- drop CTP infrastructure ------------------------------------------
    op.drop_column("products", "custom_targeting_profile_ids")
    op.drop_index("idx_custom_targeting_profiles_tenant", table_name="custom_targeting_profiles")
    op.drop_table("custom_targeting_profiles")

    # ---- new tenant_signals table -----------------------------------------
    op.create_table(
        "tenant_signals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(50),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        # AdCP SignalValueType: 'binary' | 'categorical' | 'numeric'
        sa.Column("value_type", sa.String(32), nullable=False),
        # Categorical taxonomy (when value_type='categorical').
        sa.Column(
            "categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Numeric bounds (when value_type='numeric'). NULL when N/A.
        sa.Column("range_min", sa.Numeric(20, 6), nullable=True),
        sa.Column("range_max", sa.Numeric(20, 6), nullable=True),
        # Adapter-specific resolution map — operator-authored, opaque to the
        # storefront. Shape varies by adapter:
        #   GAM:        {"kind": "custom_key_value", "key_id": "12345",
        #                "value_ids": {"sports": "11111", "news": "22222"}}
        #             | {"kind": "audience_segment", "segment_id": "98765"}
        #   Freewheel:  {"kind": "audience_item", "audience_item_id": "..."}
        #             | {"kind": "viewership_profile", "id": "..."}
        #   Broadstreet/SpringServe: TBD
        # The per-adapter materializer reads this to produce impl_config.
        sa.Column(
            "adapter_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Where the signal comes from (publisher first-party, 3p data
        # provider, derived, …). Informational; storefront UX may surface it.
        sa.Column("data_provider", sa.String(200), nullable=True),
        # AdCP-standard targeting-dimension this signal narrows (audience,
        # contextual, weather, …). Lets the storefront cross-check against
        # InventoryProfile.constraints.targeting_dimensions.
        sa.Column(
            "targeting_dimension",
            sa.String(64),
            nullable=True,
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
        sa.UniqueConstraint("tenant_id", "signal_id", name="uq_tenant_signal"),
    )
    op.create_index("idx_tenant_signals_tenant", "tenant_signals", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("idx_tenant_signals_tenant", table_name="tenant_signals")
    op.drop_table("tenant_signals")

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
        sa.UniqueConstraint("tenant_id", "custom_targeting_profile_id", name="uq_custom_targeting_profile"),
    )
    op.create_index(
        "idx_custom_targeting_profiles_tenant",
        "custom_targeting_profiles",
        ["tenant_id"],
    )
    op.add_column(
        "products",
        sa.Column("custom_targeting_profile_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
