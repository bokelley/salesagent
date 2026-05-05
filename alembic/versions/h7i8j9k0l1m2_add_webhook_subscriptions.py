"""add webhook_subscriptions table

Revision ID: h7i8j9k0l1m2
Revises: f8c9d0a1b2c3
Create Date: 2026-05-04 23:00:00.000000

Sprint 6 of embedded-mode (``docs/design/embedded-mode-sprint-6.md``).

Adds the ``webhook_subscriptions`` table backing the Tenant Management API's
``/tenants/{tid}/webhooks`` endpoints. Each row is one outbound subscription
owned by a tenant — the salesagent POSTs lifecycle events (workflow.created,
workflow.decided, media_buy.status_changed, sync.completed, sync.failed,
tenant.config_changed) to ``url`` whenever they fire.

``secret_hash`` is the sha256 hex of the plaintext secret, which is returned
to the API caller exactly once at create time. Receivers verify HMAC-SHA256
signatures on the wire using the plaintext.

Soft-delete: DELETE flips ``is_active=false`` rather than hard-deleting the
row, so audit trails referencing the webhook stay valid.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

revision: str = "h7i8j9k0l1m2"
down_revision: str | Sequence[str] | None = "f8c9d0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "webhook_subscriptions",
        sa.Column("webhook_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=50),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("event_types", JSONType(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("extra_headers", JSONType(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_status", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_webhook_subscriptions_tenant",
        "webhook_subscriptions",
        ["tenant_id"],
    )
    op.create_index(
        "idx_webhook_subscriptions_active",
        "webhook_subscriptions",
        ["tenant_id", "is_active"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_webhook_subscriptions_active", table_name="webhook_subscriptions")
    op.drop_index("idx_webhook_subscriptions_tenant", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
