"""merge webhook visibility with main

Revision ID: 1426a7207b38
Revises: cca52ba4ec44, 393172c38f48
Create Date: 2026-05-08 11:01:58.651967

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1426a7207b38"
down_revision: Union[str, Sequence[str], None] = ("cca52ba4ec44", "393172c38f48")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
