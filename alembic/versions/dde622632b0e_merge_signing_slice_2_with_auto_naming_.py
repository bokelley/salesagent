"""merge signing slice 2 with auto-naming/gam-auth

Revision ID: dde622632b0e
Revises: o5p6q7r8s9t0, d2e3f4a5b6c7
Create Date: 2026-05-07 05:00:45.912921

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "dde622632b0e"
down_revision: Union[str, Sequence[str], None] = ("o5p6q7r8s9t0", "d2e3f4a5b6c7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
