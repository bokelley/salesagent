"""composition: drop agreed_cpm, allow null principal access_token

Refinements for the embedded composition API:

- ``products.agreed_cpm`` — drop. Pricing now flows through the existing
  ``pricing_options`` one-to-many relationship using AdCP's typed
  ``PricingOption`` shape on the wire. Storefront-set price is recorded
  as a PricingOption row alongside the Product.

- ``principals.access_token`` — make nullable. In embedded mode the host
  (Scope3) is the only agent; the sales agent never receives a request
  directly from a buyer, so the per-principal bearer token is dead code.
  Open-instance principals (legacy path) keep tokens populated.

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
Create Date: 2026-05-15

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v4w5x6y7z8a9"
down_revision: str | Sequence[str] | None = "u3v4w5x6y7z8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("products", "agreed_cpm")
    op.alter_column(
        "principals",
        "access_token",
        existing_type=sa.String(255),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "principals",
        "access_token",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.add_column(
        "products",
        sa.Column("agreed_cpm", sa.Numeric(10, 4), nullable=True),
    )
