"""add_external_identity_to_audit_log

Revision ID: 20c448890df9
Revises: b118da383e3c
Create Date: 2026-05-04 07:22:38.507848

Adds optional external-identity columns to audit_logs so mutations triggered
by upstream-platform users (Scope3 Storefront, etc.) can be attributed in the
audit trail. All four columns are nullable — open-instance audit rows
continue to leave them empty.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20c448890df9"
down_revision: Union[str, Sequence[str], None] = "b118da383e3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "audit_logs",
        sa.Column("external_user_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("external_user_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("external_org_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("external_source", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("audit_logs", "external_source")
    op.drop_column("audit_logs", "external_org_id")
    op.drop_column("audit_logs", "external_user_id")
    op.drop_column("audit_logs", "external_user_email")
