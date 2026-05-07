"""publisher_partner_aao_status_columns

Revision ID: n5o6p7q8r9s0
Revises: e9a1c2d3f4b5
Create Date: 2026-05-06 06:30:00.000000

Adds AAO status counts to ``publisher_partners`` so the Publisher Partnerships
UI can render the live "47 / 200 authorized" picture without round-tripping the
AAO on every page load:

- ``total_properties``     — count of properties the publisher lists in their
                             adagents.json (the denominator).
- ``authorized_properties`` — subset authorized to this tenant's
                             ``public_agent_url`` (the numerator).
- ``last_refreshed_at``    — when these counts were last computed (drives
                             the "refreshed N hours ago" timestamp + manual
                             refresh button).
- ``last_fetch_error``     — non-NULL when the most recent adagents.json fetch
                             failed; surfaced in the UI as the "unreachable"
                             state.

Also backfills ``public_agent_url=https://interchange.io`` on existing embedded
tenants where the column is NULL — embedded mode ships interchange as the
shared agent URL, no per-tenant override.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, Sequence[str], None] = "e9a1c2d3f4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "publisher_partners",
        sa.Column("total_properties", sa.Integer(), nullable=True),
    )
    op.add_column(
        "publisher_partners",
        sa.Column("authorized_properties", sa.Integer(), nullable=True),
    )
    op.add_column(
        "publisher_partners",
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "publisher_partners",
        sa.Column("last_fetch_error", sa.Text(), nullable=True),
    )

    # Backfill: embedded tenants with NULL public_agent_url get the shared
    # interchange.io URL. The salesagent-side default for managed mode.
    op.execute(
        """
        UPDATE tenants
        SET public_agent_url = 'https://interchange.io'
        WHERE is_embedded = TRUE
          AND public_agent_url IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("publisher_partners", "last_fetch_error")
    op.drop_column("publisher_partners", "last_refreshed_at")
    op.drop_column("publisher_partners", "authorized_properties")
    op.drop_column("publisher_partners", "total_properties")
