"""springserve_inventory_full_hierarchy

Extend the SpringServe inventory cache to capture the full supply hierarchy
(supply_partner -> supply_router -> supply_tag) plus the KV namespace catalog
(keys + value_lists). Replaces the polymorphic ``parent_id`` column with
explicit foreign-key columns so each row's relationships are unambiguous and
the common queries ("all tags in router X", "all value lists for key Y") are
single-index lookups.

Backfill: existing ``supply_tag`` rows have ``parent_id`` pointing at their
supply_partner; we copy that into the new ``supply_partner_id`` column before
dropping ``parent_id``. ``supply_router_id`` stays NULL on backfill -- it
gets populated on the next inventory sync run.

Revision ID: ss03f1a2b3c4
Revises: 2610e8efe918
Create Date: 2026-05-18 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ss03f1a2b3c4"
down_revision: str | Sequence[str] | None = "2610e8efe918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add explicit FK columns (all nullable -- the type discriminator
    #    determines which ones are populated for each row).
    op.add_column(
        "springserve_inventory",
        sa.Column("supply_partner_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "springserve_inventory",
        sa.Column("supply_router_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "springserve_inventory",
        sa.Column("key_id", sa.String(64), nullable=True),
    )

    # 2. Backfill from the old polymorphic parent_id:
    #    - supply_tag rows: parent_id was the supply_partner_id
    #    - supply_partner rows: parent_id was always NULL
    op.execute(
        """
        UPDATE springserve_inventory
        SET supply_partner_id = parent_id
        WHERE entity_type = 'supply_tag' AND parent_id IS NOT NULL
        """
    )

    # 3. Drop the polymorphic column now that the explicit columns cover it.
    op.drop_column("springserve_inventory", "parent_id")

    # 4. Indexes for the new lookup paths. The (tenant_id, entity_type)
    #    index already exists and continues to serve "all routers in tenant"
    #    style queries. These two cover the new join shapes.
    op.create_index(
        "idx_springserve_inventory_router",
        "springserve_inventory",
        ["tenant_id", "supply_router_id"],
    )
    op.create_index(
        "idx_springserve_inventory_key",
        "springserve_inventory",
        ["tenant_id", "key_id"],
    )

    # 5. Update the entity_type comment to reflect the new vocabulary.
    op.alter_column(
        "springserve_inventory",
        "entity_type",
        existing_type=sa.String(40),
        existing_nullable=False,
        comment=(
            "SpringServe entity kind: supply_partner, supply_router, supply_tag, "
            "key, value_list"
        ),
    )


def downgrade() -> None:
    # Restore the polymorphic column and best-effort populate it from the
    # new columns (supply_tag.parent = supply_partner). Loses router and
    # key relationships -- expected, since the old schema couldn't express
    # them. Value-list rows lose their key_id; they can be re-synced.
    op.alter_column(
        "springserve_inventory",
        "entity_type",
        existing_type=sa.String(40),
        existing_nullable=False,
        comment="SpringServe entity kind: supply_partner, supply_tag, supply_group, account",
    )
    op.drop_index("idx_springserve_inventory_key", table_name="springserve_inventory")
    op.drop_index("idx_springserve_inventory_router", table_name="springserve_inventory")
    op.add_column(
        "springserve_inventory",
        sa.Column("parent_id", sa.String(64), nullable=True),
    )
    op.execute(
        """
        UPDATE springserve_inventory
        SET parent_id = supply_partner_id
        WHERE entity_type = 'supply_tag' AND supply_partner_id IS NOT NULL
        """
    )
    op.drop_column("springserve_inventory", "key_id")
    op.drop_column("springserve_inventory", "supply_router_id")
    op.drop_column("springserve_inventory", "supply_partner_id")
