"""Proposal drafts table for ProposalStore persistence.

Backs ``core.proposal.store.SalesAgentProposalStore`` (adcp 5.4.0
``LazyPlatformRouter(proposal_store_factory=…)``). The framework's
``proposal_dispatch.py`` orchestrates the four-state lifecycle
(``DRAFT → COMMITTED → CONSUMING → CONSUMED``) and calls our store
methods to persist each transition. Without durability the storyboard
step ``media_buy_seller/proposal_finalize/create_media_buy`` fails
because the buyer's ``proposal_id`` reference can't be hydrated on
multi-replica deploys.

Schema highlights:
- ``(tenant_id, proposal_id)`` is the primary lookup tuple; cross-tenant
  probes are blocked at the row level.
- ``state`` is a 4-value text column (not a Pg enum — adopters prefer
  the flexibility for migrations; see ``MediaBuy.status``).
- ``account_id`` is required (the Protocol's ``expected_account_id``
  check is row-level too).
- ``media_buy_id`` reverse-index for ``get_by_media_buy_id`` — partial
  unique on ``(tenant_id, media_buy_id)`` where ``media_buy_id IS NOT NULL``
  so legacy / non-proposal media buys don't collide.
- ``expires_at`` for TTL eviction (separate cron, not in this migration).

Revision ID: r0s1t2u3v4w5
Revises: q9r0s1t2u3v4, 8820c87e8ae3
Create Date: 2026-05-13 16:00:00.000000

Two heads existed on ``origin/main`` when this migration was authored
(``q9r0s1t2u3v4`` from the advertiser-buyer-assignment branch and
``8820c87e8ae3`` from the aao_status_kind branch). This migration
merges both heads in addition to adding the ``proposal_drafts`` table
— pinning the single-migration-head structural guard back to one.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.core.database.json_type import JSONType

revision: str = "r0s1t2u3v4w5"
down_revision: str | Sequence[str] | None = ("q9r0s1t2u3v4", "8820c87e8ae3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create proposal_drafts table."""
    op.create_table(
        "proposal_drafts",
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("proposal_id", sa.String(length=200), nullable=False),
        sa.Column("account_id", sa.String(length=200), nullable=False),
        sa.Column(
            "state",
            sa.String(length=20),
            nullable=False,
            comment="DRAFT | COMMITTED | CONSUMING | CONSUMED",
        ),
        sa.Column(
            "proposal_payload",
            JSONType,
            nullable=False,
            comment="Last-written proposal envelope (overwritten on refine/put_draft).",
        ),
        sa.Column(
            "recipes",
            JSONType,
            nullable=False,
            comment="Per-product recipe mapping (product_id → Recipe) supplied at put_draft.",
        ),
        sa.Column(
            "media_buy_id",
            sa.String(length=200),
            nullable=True,
            comment="Set by finalize_consumption — reverse-index target.",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Wall-clock cut-off after which the proposal cannot be reserved. NULL while DRAFT.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "proposal_id"),
        # Reverse-index lookup: partial unique so non-proposal media buys
        # (legacy direct create_media_buy without proposal_id) and DRAFT
        # rows that haven't reached CONSUMED don't collide.
        sa.Index(
            "ix_proposal_drafts_media_buy_id",
            "tenant_id",
            "media_buy_id",
            unique=True,
            postgresql_where=sa.text("media_buy_id IS NOT NULL"),
        ),
        # Account scoping: every Protocol method that accepts
        # ``expected_account_id`` enforces this at row level; the index
        # makes the SELECT cheap.
        sa.Index("ix_proposal_drafts_account_id", "tenant_id", "account_id"),
        # TTL sweep target (background cron, separate). State filter so
        # CONSUMED records — which keep their reverse-index value — are
        # excluded from eviction scans.
        sa.Index(
            "ix_proposal_drafts_expires_at",
            "expires_at",
            postgresql_where=sa.text("state IN ('DRAFT', 'COMMITTED')"),
        ),
    )


def downgrade() -> None:
    """Drop proposal_drafts table."""
    op.drop_index("ix_proposal_drafts_expires_at", table_name="proposal_drafts")
    op.drop_index("ix_proposal_drafts_account_id", table_name="proposal_drafts")
    op.drop_index("ix_proposal_drafts_media_buy_id", table_name="proposal_drafts")
    op.drop_table("proposal_drafts")
