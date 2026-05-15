"""swap proposals table to PgProposalStore-compatible schema

adcp 5.5.0 (adcontextprotocol/adcp-client-python#732) ships
:class:`PgProposalStore` — the durable ``ProposalStore`` Protocol
implementation that subsumes our local :class:`SalesAgentProposalStore`.
This migration replaces our locally-shaped ``proposals`` table with the
upstream-compatible schema so we can wire the framework-owned store
without forking it.

Differences from the prior schema (r0s1t2u3v4w5):

* **PK shape**: ``(account_id, proposal_id)`` instead of bare
  ``(proposal_id)``. Upstream's ``INSERT ... ON CONFLICT (account_id,
  proposal_id)`` requires the compound unique key. Our existing
  ``proposal_id``-only PK was stricter than necessary (same proposal_id
  in two accounts would collide where it shouldn't).
* **``state`` CHECK constraint**: ``CHECK (state IN ('draft',
  'committed', 'consuming', 'consumed'))``. Was application-layer-only
  before; lifting it to DB-layer prevents corrupt writes from bypassing
  the state machine.
* **``account_id`` / ``proposal_id`` / ``media_buy_id`` collation**:
  ``COLLATE "C"`` for byte-order index ordering. Faster than the default
  locale-aware collation for the ``FOR UPDATE`` row locks the upstream
  CAS performs.
* **``ix_proposals_expires_at`` partial index**: drives the upstream's
  TTL-based cleanup sweep (committed-state hold expiry). Was missing.
* **``tenant_id`` as a generated column**: derived from
  ``account_id`` via ``split_part(account_id, ':', 1)``. Preserves the
  existing FK + ``ON DELETE CASCADE`` semantics that
  :func:`scripts.seed_demo_tenant._delete_tenant_rows` and
  :func:`src.admin.tenant_management_api.delete_tenant` already rely on,
  without forcing those code paths to learn about the proposals table's
  internal layout.

The generated-column choice is salesagent-internal and consistent with
the upstream layering principle established by
adcontextprotocol/adcp-client-python#738 — the encoding seam stays at
:class:`SalesagentAccountStore.resolve()`, and we leverage the same
encoding inside our own table for our own FK target. ``PgProposalStore``
uses explicit-column INSERTs (``INSERT INTO proposals (account_id,
proposal_id, state, recipes, proposal_payload, ...)``), so the generated
column is invisible to upstream — the store doesn't know it exists.

## Data loss

This drops the existing ``proposals`` table. PR #390 created it ~24h
before this migration; near-zero production data. Compliance probes that
ran against the old schema produced ephemeral rows that the framework
no longer needs (the proposals they reference were already consumed or
expired). Acceptable loss; the schema correctness win outweighs.

Revision ID: t2u3v4w5x6y7
Revises: ss02e5f6a7b8
Create Date: 2026-05-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "t2u3v4w5x6y7"
# Rebased onto ss02e5f6a7b8 (SpringServe inventory migration that landed
# on main during this PR's review cycle). Original parent was d0c3c40fdd41;
# the springserve chain (ss01a1b2c3d4 → ss02e5f6a7b8) doesn't touch
# proposals, so linearizing instead of using ``alembic merge`` keeps
# the history cleaner.
down_revision: str | Sequence[str] | None = "ss02e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the existing table. SalesAgentProposalStore is being replaced
    # wholesale; the prior schema's data is not migrated.
    op.execute("DROP TABLE IF EXISTS proposals CASCADE")

    op.create_table(
        "proposals",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "recipes",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("proposal_payload", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_buy_id", sa.Text(), nullable=True),
        sa.Column(
            "recipe_schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("account_id", "proposal_id"),
        sa.CheckConstraint(
            "state IN ('draft', 'committed', 'consuming', 'consumed')",
            name="ck_proposals_state",
        ),
    )

    # Fix up account_id / proposal_id / media_buy_id collations to "C"
    # (byte-order). Alembic's SQLAlchemy bindings don't expose
    # `COLLATE "C"` cleanly at create_table time, so apply via ALTER.
    op.execute('ALTER TABLE proposals ALTER COLUMN account_id TYPE TEXT COLLATE "C"')
    op.execute('ALTER TABLE proposals ALTER COLUMN proposal_id TYPE TEXT COLLATE "C"')
    op.execute('ALTER TABLE proposals ALTER COLUMN media_buy_id TYPE TEXT COLLATE "C"')

    # Generated ``tenant_id`` column derived from the AccountStore-minted
    # ``account_id`` compound. ``split_part(..., ':', 1)`` returns the
    # whole string when no ``:`` is present, so non-compound account_ids
    # (legacy callers / direct admin tooling) will fail the FK loudly
    # rather than silently inserting under a phantom tenant.
    op.execute(
        "ALTER TABLE proposals ADD COLUMN tenant_id VARCHAR(50) "
        "GENERATED ALWAYS AS (split_part(account_id, ':', 1)) STORED"
    )
    op.create_foreign_key(
        "fk_proposals_tenant_id",
        "proposals",
        "tenants",
        ["tenant_id"],
        ["tenant_id"],
        ondelete="CASCADE",
    )

    # Reverse-index lookup constraint per the ProposalStore Protocol's
    # ``get_by_media_buy_id``. Partial because pre-consumption rows
    # have NULL ``media_buy_id`` and must not be rejected.
    op.create_index(
        "ux_proposals_account_media_buy",
        "proposals",
        ["account_id", "media_buy_id"],
        unique=True,
        postgresql_where=sa.text("media_buy_id IS NOT NULL"),
    )
    # Drives TTL-based cleanup of expired committed-state holds.
    op.create_index(
        "ix_proposals_expires_at",
        "proposals",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )
    # Secondary for tenant-scoped admin queries (debug dashboard, audit).
    op.create_index("ix_proposals_tenant_id", "proposals", ["tenant_id"])


def downgrade() -> None:
    # Drop the table; revert to the prior r0s1t2u3v4w5 shape. Note: the
    # forward migration drops the original table, so downgrade can't
    # restore data — it only restores the schema shape.
    op.drop_constraint("fk_proposals_tenant_id", "proposals", type_="foreignkey")
    op.drop_index("ix_proposals_tenant_id", table_name="proposals")
    op.drop_index("ix_proposals_expires_at", table_name="proposals")
    op.drop_index("ux_proposals_account_media_buy", table_name="proposals")
    op.execute("DROP TABLE IF EXISTS proposals CASCADE")

    op.create_table(
        "proposals",
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "recipes",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("proposal_payload", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_buy_id", sa.String(64), nullable=True),
        sa.Column("recipe_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index(
        "ux_proposals_account_media_buy",
        "proposals",
        ["account_id", "media_buy_id"],
        unique=True,
        postgresql_where=sa.text("media_buy_id IS NOT NULL"),
    )
    op.create_index("ix_proposals_tenant_id", "proposals", ["tenant_id"])
