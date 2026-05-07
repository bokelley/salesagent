"""Merge phase1 slice 2 with latest main

Revision ID: 523ed762edce
Revises: e0f450f098de, ff743db9ac90
Create Date: 2026-05-07 13:41:40.516932

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "523ed762edce"
down_revision: Union[str, Sequence[str], None] = ("e0f450f098de", "ff743db9ac90")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
