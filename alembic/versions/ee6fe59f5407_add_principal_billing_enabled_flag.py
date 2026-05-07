"""add principal billing_enabled flag

Revision ID: ee6fe59f5407
Revises: e9f8a7ce4e46
Create Date: 2026-05-07 09:23:00.666427

Slice 4 of the per-buyer-agent refactor. Adds ``principals.billing_enabled``
so an operator can mark some buyer agents as billing-eligible (default) and
others as exempt (internal/free-tier/test agents).

When ``billing_enabled=False``, the account create/update path rejects any
request that sets ``Account.billing = "agent"`` for an account owned by
this principal. ``billing="operator"`` (or NULL) is always allowed.

Default true preserves existing behavior on rollout.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ee6fe59f5407"
down_revision: str | Sequence[str] | None = "e9f8a7ce4e46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "principals",
        sa.Column(
            "billing_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("principals", "billing_enabled")
