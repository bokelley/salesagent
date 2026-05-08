"""Add partial unique index on tenant_signing_credentials active rows

Revision ID: 8c4e44fda739
Revises: 393172c38f48
Create Date: 2026-05-08 12:13:41.879198

Enforces the at-most-one-active invariant the repository docstring
already assumes. Without this index, two concurrent admin sessions
(or two replicas) calling ``rotate_out`` + ``create`` end up with two
``is_active=True`` rows for the same ``(tenant_id, purpose)`` —
``get_active`` then returns whichever ``ORDER BY created_at`` sorts
first while the operator-published JWKS lists the other, and half
the buyer verifications fail until someone manually inactivates one.

The cache invalidation listener (added in PR #195) closes the read-side
race; this index closes the write-side race.

PostgreSQL partial unique index syntax — ``WHERE is_active = TRUE``.
The index is small (only active rows) and fast to maintain.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c4e44fda739"
down_revision: tuple[str, ...] | str | Sequence[str] | None = "393172c38f48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ux_tenant_signing_credentials_active",
        "tenant_signing_credentials",
        ["tenant_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_tenant_signing_credentials_active",
        table_name="tenant_signing_credentials",
    )
