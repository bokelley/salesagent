"""add_managed_mode_to_tenant

Revision ID: b118da383e3c
Revises: b4e2bffdd4f8
Create Date: 2026-05-04 07:22:35.695119

Adds platform-managed-tenant fields to the tenants table:
- managed_externally: when true, the tenant's platform-managed surfaces are
  locked to the Tenant Management API (the model-layer write guard enforces
  this).
- external_org_id: identifier from the upstream platform (e.g. Scope3) that
  owns this tenant. Indexed but not unique — a single org may map to multiple
  tenants in the future.
- external_source: name of the upstream platform (e.g. "scope3").
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b118da383e3c"
down_revision: Union[str, Sequence[str], None] = "b4e2bffdd4f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tenants",
        sa.Column(
            "managed_externally",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("external_org_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("external_source", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_tenants_external_org_id",
        "tenants",
        ["external_org_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_tenants_external_org_id", table_name="tenants")
    op.drop_column("tenants", "external_source")
    op.drop_column("tenants", "external_org_id")
    op.drop_column("tenants", "managed_externally")
