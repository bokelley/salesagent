"""add request_payload and response_body to webhook_delivery_log

The existing ``webhook_delivery_log`` table tracks delivery-report
webhook fires with metadata (status, http_status_code, attempt_count,
error_message, payload_size_bytes, response_time_ms). For #101 buyer
self-debug visibility via ``get_media_buys``
``ext.psa.include_webhook_activity``, we also need the actual body that
was sent and the response body that was received. Both are nullable —
older rows pre-date this column, and non-HTTP failures (connection
refused, timeout) have no response body.

Truncation to ~64KB is enforced at insert time by the persistence
helper, not by the schema (Postgres TEXT has no length limit).

Revision ID: cca52ba4ec44
Revises: f81308a72e28
Create Date: 2026-05-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

revision: str = "cca52ba4ec44"
down_revision: str | Sequence[str] | None = "f81308a72e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_delivery_log",
        sa.Column("request_payload", JSONType(), nullable=True),
    )
    op.add_column(
        "webhook_delivery_log",
        sa.Column("response_body", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_delivery_log", "response_body")
    op.drop_column("webhook_delivery_log", "request_payload")
