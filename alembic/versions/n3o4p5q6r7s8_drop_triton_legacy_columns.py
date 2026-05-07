"""drop legacy triton columns from adapter_config

Revision ID: n3o4p5q6r7s8
Revises: e9a1c2d3f4b5
Create Date: 2026-05-06 00:00:00.000000

The Triton adapter is being rebuilt against the real TAP Media Buying API
(``mbapi.tritondigital.com`` with publisher-scoped JWT auth). The legacy
``triton_station_id`` and ``triton_api_key`` columns modelled a single-station
static-bearer assumption that does not match TAP — a publisher owns many
stations, and station selection is a flight-level targeting dimension, not a
connection credential. New configuration lives in ``AdapterConfig.config_json``
under the schema-driven adapter framework.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n3o4p5q6r7s8"
down_revision: str | Sequence[str] | None = "e9a1c2d3f4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("adapter_config", schema=None) as batch_op:
        batch_op.drop_column("triton_station_id")
        batch_op.drop_column("triton_api_key")


def downgrade() -> None:
    with op.batch_alter_table("adapter_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("triton_station_id", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("triton_api_key", sa.String(100), nullable=True))
