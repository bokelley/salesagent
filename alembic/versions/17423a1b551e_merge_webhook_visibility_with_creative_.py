"""merge webhook visibility with creative pre-approval gate

Revision ID: 17423a1b551e
Revises: 084b4e6afd8d, d1262dff8a49
Create Date: 2026-05-08 18:56:29.253914

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "17423a1b551e"
down_revision: Union[str, Sequence[str], None] = ("084b4e6afd8d", "d1262dff8a49")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
