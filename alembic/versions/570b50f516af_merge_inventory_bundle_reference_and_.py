"""merge inventory_bundle_reference and springserve heads

Revision ID: 570b50f516af
Revises: a9b0c1d2e3f4, ss03f1a2b3c4
Create Date: 2026-05-19 04:19:09.859268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '570b50f516af'
down_revision: Union[str, Sequence[str], None] = ('a9b0c1d2e3f4', 'ss03f1a2b3c4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
