"""merge webhook visibility with main 2nd round

Revision ID: 084b4e6afd8d
Revises: 1426a7207b38, 8c4e44fda739
Create Date: 2026-05-08 14:55:10.941670

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "084b4e6afd8d"
down_revision: Union[str, Sequence[str], None] = ("1426a7207b38", "8c4e44fda739")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
