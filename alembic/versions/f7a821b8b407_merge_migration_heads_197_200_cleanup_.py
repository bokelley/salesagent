"""Merge migration heads (#197 + #200 + cleanup branch)

Revision ID: f7a821b8b407
Revises: d78517343f45, dc7ad64fff72
Create Date: 2026-05-08 09:14:38.498544

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a821b8b407"
down_revision: Union[str, Sequence[str], None] = ("d78517343f45", "dc7ad64fff72")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
