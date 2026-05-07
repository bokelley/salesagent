"""drop tenants.house_domain

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-05-06 09:30:00.000000

The ``tenant.house_domain`` column was a vestige of the single-publisher
salesagent model where one tenant === one publisher. In multi-publisher
deployments (sales houses representing many publishers) the field has no
load-bearing meaning: each ``PublisherPartner.publisher_domain`` is its
own house (their ``brand.json`` and ``adagents.json`` live there), and the
operator's signing identity lives in the separate ``Tenant.brand_json_url``
column.

Buyer-facing inventory discovery goes directly to each publisher's
``brand.json`` via the AAO; the salesagent never aggregates property
lists, so it doesn't need a tenant-level "where my brand.json lives" hint.

Self-publishing tenants (Wonderstruck running their own salesagent) lose
nothing: they already appear as a ``PublisherPartner`` row alongside their
own agent URL.

``AdmittedOperator.house_domain`` (the signing trust graph's per-operator
domain) is a separate column on a different table — untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, Sequence[str], None] = "n5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop ``tenants.house_domain``."""
    op.drop_column("tenants", "house_domain")


def downgrade() -> None:
    """Re-add ``tenants.house_domain`` as nullable.

    **Data is gone.** Upgrade dropped the column; downgrade restores the
    column shape only — original values cannot be recovered. Acceptable
    because the column was dead by design (the per-publisher
    ``PublisherPartner.publisher_domain`` rows + the operator-side
    ``Tenant.brand_json_url`` cover what it tracked). If you need values
    back, restore from a pre-upgrade backup.
    """
    op.add_column(
        "tenants",
        sa.Column("house_domain", sa.String(length=255), nullable=True),
    )
