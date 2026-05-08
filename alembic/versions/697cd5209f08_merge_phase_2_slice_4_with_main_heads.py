"""Merge phase 2 slice 4 with main heads

Revision ID: 697cd5209f08
Revises: d78517343f45, dc7ad64fff72
Create Date: 2026-05-08 09:13:05.901604

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "697cd5209f08"
down_revision: Union[str, Sequence[str], None] = ("d78517343f45", "dc7ad64fff72")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
