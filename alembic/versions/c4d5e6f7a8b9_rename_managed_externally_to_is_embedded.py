"""rename managed_externally to is_embedded

Revision ID: c4d5e6f7a8b9
Revises: e7a4c2b9d5f1
Create Date: 2026-05-04 19:00:00.000000

Rename ``tenants.managed_externally`` to ``tenants.is_embedded``.

The column is being renamed alongside the broader "managed mode" →
"embedded mode" terminology sweep. PSA can be embedded into a host
product (Scope3, Manticore, any future host) — "embedded" describes
that more accurately than "managed externally", which collided
semantically with ``is_active`` lifecycle on the Tenant.

Pure column rename — no data migration, no default change.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "e7a4c2b9d5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "tenants",
        "managed_externally",
        new_column_name="is_embedded",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "tenants",
        "is_embedded",
        new_column_name="managed_externally",
    )
