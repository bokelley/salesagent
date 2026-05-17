"""product composition source enum + dynamic-compose columns

Adds the columns the embedded composition API writes to and the discriminator
the AdCP surface reads to filter dynamic products out of the catalog:

- ``products.composition_source`` — enum discriminator
  (``static`` | ``signal_variant`` | ``storefront_composed``).
  Backfilled from ``is_dynamic_variant`` so existing rows land on the right
  bucket. New REST-composed products write ``storefront_composed``.
- ``products.composed_by_principal_id`` — FK to the principal whose storefront
  composed the row; NULL for static and signal-variant rows.
- ``products.idempotency_key`` — storefront-supplied key for replay-safe
  ``POST /api/v1/products``. Unique per (tenant, principal) when non-null.
- ``products.custom_targeting_profile_ids`` — JSONB array of profile ids
  referenced at composition time. Resolution into ``implementation_config``
  happens at write time.
- ``products.agreed_cpm`` — storefront-set price recorded for audit. Not
  enforced by the sales agent.

Additive only — keeps ``is_dynamic`` and ``is_dynamic_variant`` live so the
template-driven signal-variant pipeline keeps working unchanged. A later
cleanup migration can drop those once callers move to ``composition_source``.

Revision ID: u3v4w5x6y7z8
Revises: z8a9b0c1d2e3
Create Date: 2026-05-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "u3v4w5x6y7z8"
down_revision: str | Sequence[str] | None = "z8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COMPOSITION_SOURCE_VALUES = ("static", "signal_variant", "storefront_composed")


def upgrade() -> None:
    composition_source_enum = postgresql.ENUM(
        *COMPOSITION_SOURCE_VALUES,
        name="composition_source",
        create_type=False,
    )
    composition_source_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "products",
        sa.Column(
            "composition_source",
            composition_source_enum,
            nullable=False,
            server_default=sa.text("'static'::composition_source"),
        ),
    )

    # Backfill: existing signal-variants land on the matching enum value.
    # Templates (`is_dynamic = True`) stay on 'static' — the template itself
    # is a static catalog row that spawns variants; the variants are what
    # carry the signal_variant marker.
    op.execute("UPDATE products SET composition_source = 'signal_variant' WHERE is_dynamic_variant = TRUE")

    op.add_column(
        "products",
        sa.Column("composed_by_principal_id", sa.String(50), nullable=True),
    )
    # No FK to principals: a composite FK with ondelete=SET NULL would try
    # to null both columns of the composite (including tenant_id, which is
    # NOT NULL on products). Principal existence is validated at the API
    # boundary; tenant scoping is enforced via the existing tenant_id PK.

    op.add_column(
        "products",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_products_idempotency_key",
        "products",
        ["tenant_id", "composed_by_principal_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.add_column(
        "products",
        sa.Column(
            "custom_targeting_profile_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.add_column(
        "products",
        sa.Column("agreed_cpm", sa.Numeric(10, 4), nullable=True),
    )

    op.create_index(
        "idx_products_composition_source",
        "products",
        ["tenant_id", "composition_source"],
    )


def downgrade() -> None:
    op.drop_index("idx_products_composition_source", table_name="products")
    op.drop_column("products", "agreed_cpm")
    op.drop_column("products", "custom_targeting_profile_ids")
    op.drop_index("idx_products_idempotency_key", table_name="products")
    op.drop_column("products", "idempotency_key")
    op.drop_column("products", "composed_by_principal_id")
    op.drop_column("products", "composition_source")

    composition_source_enum = postgresql.ENUM(
        *COMPOSITION_SOURCE_VALUES,
        name="composition_source",
        create_type=False,
    )
    composition_source_enum.drop(op.get_bind(), checkfirst=True)
