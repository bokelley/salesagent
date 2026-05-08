"""merge advertiser_buyer_assignment with fix_duplication

Revision ID: 6daaddad5e2a
Revises: 0fa8fa8610df, q9r0s1t2u3v4
Create Date: 2026-05-07 22:48:33.322767

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6daaddad5e2a"
down_revision: str | Sequence[str] | None = ("0fa8fa8610df", "q9r0s1t2u3v4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
