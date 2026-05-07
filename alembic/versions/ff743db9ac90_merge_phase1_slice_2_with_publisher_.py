"""Merge phase1 slice 2 with publisher partner status

Revision ID: ff743db9ac90
Revises: 102ce62707b9, o6p7q8r9s0t1
Create Date: 2026-05-07 11:43:01.538329

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ff743db9ac90"
down_revision: Union[str, Sequence[str], None] = ("102ce62707b9", "o6p7q8r9s0t1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
