"""merge slice 3 with advertiser-buyer-assignment heads

Revision ID: 8ba01ca790ca
Revises: 08b37366d479, 6daaddad5e2a
Create Date: 2026-05-07 23:07:29.729123

No-op merge migration converging slice 3's signing_mode head with main's
post-#136 / post-#188 advertiser-buyer-assignment head. Both ancestors are
schema-stable; the divergence is purely the ordering of parallel PRs
landing on main while slice 3 was in review. Empty upgrade/downgrade —
the canonical no-op merge shape.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

revision: str = "8ba01ca790ca"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "08b37366d479",
    "6daaddad5e2a",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
