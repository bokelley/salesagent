"""Merge migration heads (#193 gam-projection + #186 ResolvedProduct)

Revision ID: d78517343f45
Revises: 51a885014fac, f81308a72e28
Create Date: 2026-05-08 08:15:28.142034

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d78517343f45"
down_revision: str | Sequence[str] | None = ("51a885014fac", "f81308a72e28")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
