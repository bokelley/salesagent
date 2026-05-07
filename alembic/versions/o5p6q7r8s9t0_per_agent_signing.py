"""per-agent signing — drop operator trust model, agents are first-class

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-05-06 12:00:00.000000

Slice 2 of the buyer-agents-as-first-class refactor (supersedes the
operator-trust model from PR #39).

Drops:

* ``admitted_operators`` table — operators are no longer the trust root.
* ``operator_advertiser_link`` table — billing-mode lives on ``Account``
  (already-existing ``Account.billing`` ``operator|agent`` field handles
  this case).
* ``principals.bound_operator_id`` column — replaced by per-agent
  ``agent_url``.

Adds:

* ``principals.agent_url VARCHAR(2048) NULL`` — when set, this principal
  is signature-capable; the verifier resolves JWKS by fetching
  ``<agent_url>/.well-known/jwks.json`` (convention) and verifies inbound
  signatures against it. NULL means bearer-only legacy auth.

Trust model: the salesagent trusts the buyer agent for its operator
identity (no brand.json chain walk, no operator-attestation step). This
is the user-locked design; can be evolved later if cross-org delegation
becomes a concern.

Production tables targeted by the drop are empty (PR #39 only just
landed; no tenants have admitted operators or links). Reversing this
migration restores the empty schema; no data loss.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

revision: str = "o5p6q7r8s9t0"
down_revision: str | Sequence[str] | None = "e77030648663"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "principals",
        sa.Column("agent_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "principals",
        sa.Column("brand_domain", sa.String(length=253), nullable=True),
    )
    op.add_column(
        "principals",
        sa.Column(
            "signing_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "principals",
        sa.Column("last_signed_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_principals_agent_url",
        "principals",
        ["tenant_id", "agent_url"],
        postgresql_where=sa.text("agent_url IS NOT NULL"),
    )

    # Hot path for the buyer-agent edit-page status banner: lookup of the
    # most recent verified-signature audit row for a principal. Without this
    # index, the query in AuditLogRepository.last_signed_verification_for_principal
    # is a sequential scan of audit_logs on every page load.
    op.create_index(
        "idx_audit_logs_principal_verified",
        "audit_logs",
        ["tenant_id", "principal_id", "timestamp"],
        postgresql_where=sa.text("verified_agent_url IS NOT NULL"),
    )

    # Drop bound_operator_id (operator trust path retired)
    op.drop_index("idx_principals_bound_operator", table_name="principals")
    op.drop_column("principals", "bound_operator_id")

    # Drop audit_logs.verified_operator_id — the per-agent model identifies
    # the caller via principal_id (already on the row); the operator concept
    # is no longer in the auth chain. verified_agent_url + verified_key_id
    # remain to mark signed-vs-bearer rows.
    op.drop_index("idx_audit_logs_verified_operator", table_name="audit_logs")
    op.drop_column("audit_logs", "verified_operator_id")

    # Drop the operator tables. operator_advertiser_link FK-cascades from
    # admitted_operators, so dropping in dependency order.
    op.drop_table("operator_advertiser_link")
    op.drop_index("idx_admitted_operators_active", table_name="admitted_operators")
    op.drop_table("admitted_operators")


def downgrade() -> None:
    # Recreate admitted_operators
    op.create_table(
        "admitted_operators",
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("operator_id", sa.String(length=50), nullable=False),
        sa.Column("brand_json_url", sa.String(length=2048), nullable=False),
        sa.Column("aao_member_slug", sa.String(length=200), nullable=True),
        sa.Column("house_domain", sa.String(length=253), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("is_trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_resolution_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "operator_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "brand_json_url", name="uq_admitted_operators_brand_json"),
    )
    op.create_index(
        "idx_admitted_operators_active",
        "admitted_operators",
        ["tenant_id", "is_active"],
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "operator_advertiser_link",
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("operator_id", sa.String(length=50), nullable=False),
        sa.Column("principal_id", sa.String(length=50), nullable=False),
        sa.Column(
            "billing_mode",
            sa.String(length=32),
            nullable=False,
            server_default="operator_bills",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "operator_id", "principal_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "operator_id"],
            ["admitted_operators.tenant_id", "admitted_operators.operator_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "billing_mode IN ('operator_bills', 'agent_billed', 'disabled')",
            name="chk_operator_advertiser_link_billing_mode",
        ),
    )

    op.add_column(
        "principals",
        sa.Column("bound_operator_id", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "idx_principals_bound_operator",
        "principals",
        ["tenant_id", "bound_operator_id"],
    )

    # Recreate audit_logs.verified_operator_id
    op.add_column(
        "audit_logs",
        sa.Column("verified_operator_id", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "idx_audit_logs_verified_operator",
        "audit_logs",
        ["tenant_id", "verified_operator_id"],
        postgresql_where=sa.text("verified_operator_id IS NOT NULL"),
    )

    op.drop_index("idx_audit_logs_principal_verified", table_name="audit_logs")
    op.drop_index("idx_principals_agent_url", table_name="principals")
    op.drop_column("principals", "last_signed_verified_at")
    op.drop_column("principals", "signing_required")
    op.drop_column("principals", "brand_domain")
    op.drop_column("principals", "agent_url")


# Suppress unused import warning — JSONType is referenced by the upstream
# migration's table definition and kept for parity in downgrade().
_ = JSONType
