"""add creative pre approval gate flag

Adds the per-tenant ``creative_pre_approval_gate_enabled`` feature flag
gating the creative pre-approval workflow (#145).

When enabled, creatives that arrive inline with ``create_media_buy`` or
via ``sync_creatives`` are NOT pushed to the ad server at buy-create
time when their local status is ``pending_review``. They land locally
and the adapter's creative upload + line-item-creative-association
only fires when a publisher human flips the local status to
``approved`` via ``/admin/.../creatives/<id>/approve``.

Default ``false`` — disabled-state preserves today's execute-then-gate
behaviour byte-for-byte.

Revision ID: d1262dff8a49
Revises: 8c4e44fda739
Create Date: 2026-05-08 15:07:52.604782

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1262dff8a49"
down_revision: str | Sequence[str] | None = "8c4e44fda739"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "creative_pre_approval_gate_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "creative_pre_approval_gate_enabled")
