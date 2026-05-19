"""rename inventory_review_state → inventory_bundle_reference; drop skip columns

PR-1 of #485 modeled inventory coverage as a review-and-skip workflow
("pending → in_bundle / pending → explicitly_skipped"). The framing was
wrong: operators don't review-each-ad-unit, they author inventory bundles
and the same placement gets reused across many bundles. The "explicitly
skipped" status, ``reviewed_at`` / ``reviewed_by`` audit columns, and the
state machine itself don't fit how the work actually gets done.

This migration:

* Renames the table to ``inventory_bundle_reference`` so the schema name
  matches what it actually represents — a row's existence means the
  entity is referenced by ≥1 ``InventoryProfile``.
* Drops ``status``, ``reviewed_at``, ``reviewed_by``. No more state
  machine, no audit trail (the only state is "is referenced").
* Renames the indexes + unique constraint to match.

Catching the wrong abstraction before #486 (signal coverage) mirrors it.

Revision ID: a9b0c1d2e3f4
Revises: 2610e8efe918
Create Date: 2026-05-18

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "2610e8efe918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the obsolete review/audit columns first. ``status`` was a 3-value
    # state machine (pending / in_bundle / explicitly_skipped); now row
    # existence is the only state.
    op.drop_index("idx_inventory_review_state_tenant_type_status", table_name="inventory_review_state")
    op.drop_index("idx_inventory_review_state_tenant_adapter_type", table_name="inventory_review_state")
    op.drop_constraint("uq_inventory_review_state", "inventory_review_state", type_="unique")
    op.drop_column("inventory_review_state", "status")
    op.drop_column("inventory_review_state", "reviewed_at")
    op.drop_column("inventory_review_state", "reviewed_by")

    # Rename the table to match the new semantics.
    op.rename_table("inventory_review_state", "inventory_bundle_reference")

    # Re-add the unique constraint + indexes under the new names.
    op.create_unique_constraint(
        "uq_inventory_bundle_reference",
        "inventory_bundle_reference",
        ["tenant_id", "adapter", "entity_type", "external_id"],
    )
    op.create_index(
        "idx_inventory_bundle_reference_tenant_type",
        "inventory_bundle_reference",
        ["tenant_id", "entity_type"],
    )
    op.create_index(
        "idx_inventory_bundle_reference_tenant_adapter_type",
        "inventory_bundle_reference",
        ["tenant_id", "adapter", "entity_type"],
    )


def downgrade() -> None:
    import sqlalchemy as sa

    op.drop_index("idx_inventory_bundle_reference_tenant_adapter_type", table_name="inventory_bundle_reference")
    op.drop_index("idx_inventory_bundle_reference_tenant_type", table_name="inventory_bundle_reference")
    op.drop_constraint("uq_inventory_bundle_reference", "inventory_bundle_reference", type_="unique")
    op.rename_table("inventory_bundle_reference", "inventory_review_state")
    # Restore the dropped columns. ``status`` defaults to ``in_bundle`` since
    # every surviving row is by definition bundle-referenced.
    op.add_column(
        "inventory_review_state",
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'in_bundle'")),
    )
    op.add_column(
        "inventory_review_state",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inventory_review_state",
        sa.Column("reviewed_by", sa.String(200), nullable=True),
    )
    op.create_unique_constraint(
        "uq_inventory_review_state",
        "inventory_review_state",
        ["tenant_id", "adapter", "entity_type", "external_id"],
    )
    op.create_index(
        "idx_inventory_review_state_tenant_type_status",
        "inventory_review_state",
        ["tenant_id", "entity_type", "status"],
    )
    op.create_index(
        "idx_inventory_review_state_tenant_adapter_type",
        "inventory_review_state",
        ["tenant_id", "adapter", "entity_type"],
    )
