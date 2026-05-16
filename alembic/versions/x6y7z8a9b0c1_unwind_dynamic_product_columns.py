"""unwind dynamic-product columns; composition collapses into create_media_buy

The earlier branch design added a ``POST /api/v1/.../products`` compose
endpoint that materialized "storefront-composed" Product rows with their
own composition_source, composed_by_principal_id, idempotency_key, and a
tenant-scoped max_composition_ttl. With the AdCP-pure model the storefront
composes by building a ``CreateMediaBuyRequest`` directly:

- Wholesale product = non-guaranteed ``Product`` with a $0-floor
  ``PricingOption`` (or any floor); discovered via ``get_products``.
- Signals = operator-declared ``TenantSignal`` rows surfaced via
  ``get_signals``; layered on the buy through
  ``PackageRequest.targeting_overlay``.
- Optimization = ``PackageRequest.optimization_goals`` /
  ``performance_standards`` / ``pacing`` / ``bid_price``.
- Composition step is no longer a separate write; ``create_media_buy``
  is the only buy-side write.

The dynamic-product columns become dead weight. ``TenantSignal``,
``InventoryProfile.constraints`` / ``etag``, ``Principal.external_id``,
``Principal.access_token`` nullable, and the new advertiser-mapping
infrastructure all stay — they're operator-authoring scaffolding that
slots cleanly into the AdCP-pure flow.

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0
Create Date: 2026-05-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "x6y7z8a9b0c1"
down_revision: str | Sequence[str] | None = "w5x6y7z8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- products: drop the dynamic-product columns -----------------------
    op.drop_index("idx_products_composition_source", table_name="products")
    op.drop_index("idx_products_idempotency_key", table_name="products")
    op.drop_column("products", "idempotency_key")
    op.drop_column("products", "composed_by_principal_id")
    op.drop_column("products", "composition_source")

    composition_source_enum = postgresql.ENUM(
        "static",
        "signal_variant",
        "storefront_composed",
        name="composition_source",
        create_type=False,
    )
    composition_source_enum.drop(op.get_bind(), checkfirst=True)

    # ---- tenants: drop the composition-TTL knob ---------------------------
    op.drop_column("tenants", "max_composition_ttl_seconds")


def downgrade() -> None:
    composition_source_enum = postgresql.ENUM(
        "static",
        "signal_variant",
        "storefront_composed",
        name="composition_source",
        create_type=False,
    )
    composition_source_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "tenants",
        sa.Column(
            "max_composition_ttl_seconds",
            sa.Integer,
            nullable=False,
            server_default=sa.text("604800"),
        ),
    )

    op.add_column(
        "products",
        sa.Column(
            "composition_source",
            composition_source_enum,
            nullable=False,
            server_default=sa.text("'static'::composition_source"),
        ),
    )
    op.execute("UPDATE products SET composition_source = 'signal_variant' WHERE is_dynamic_variant = TRUE")

    op.add_column(
        "products",
        sa.Column("composed_by_principal_id", sa.String(50), nullable=True),
    )
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
    op.create_index(
        "idx_products_composition_source",
        "products",
        ["tenant_id", "composition_source"],
    )
