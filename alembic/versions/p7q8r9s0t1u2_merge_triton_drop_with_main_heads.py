"""merge triton drop with main heads (kevel-drop-merge + house_domain-drop)

Revision ID: p7q8r9s0t1u2
Revises: 102ce62707b9, n3o4p5q6r7s8, o6p7q8r9s0t1
Create Date: 2026-05-07 13:00:00.000000

No-op merge migration to converge three independent heads:

- ``102ce62707b9`` — merge of issue-71 reporting_capabilities backfill +
  Kevel adapter column drop (PR #110 + PR #111).
- ``n3o4p5q6r7s8`` — drop legacy triton_station_id / triton_api_key columns
  ahead of the TAP rebuild (this branch).
- ``o6p7q8r9s0t1`` — drop tenants.house_domain (PR #78 — AAO model means
  tenant-level house_domain has no load-bearing meaning).

All three are independent column-drops on different tables, no schema
overlap. Empty upgrade/downgrade is the canonical no-op merge shape.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401  # required by alembic even for no-op migrations

revision: str = "p7q8r9s0t1u2"
down_revision: tuple[str, ...] | str | Sequence[str] | None = (
    "102ce62707b9",
    "n3o4p5q6r7s8",
    "o6p7q8r9s0t1",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
