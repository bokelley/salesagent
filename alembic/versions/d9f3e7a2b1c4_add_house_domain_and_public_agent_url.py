"""add_house_domain_and_public_agent_url_to_tenant

Revision ID: d9f3e7a2b1c4
Revises: c8a5e1d3f4b9
Create Date: 2026-05-04 17:35:00.000000

Sprint 1.7: replaces the manually-maintained AuthorizedProperty list with
on-demand AAO brand.json + adagents.json lookups (see
docs/design/replace-authorized-properties-with-aao-lookup.md).

Two new nullable Tenant columns:

- ``house_domain`` — where the publisher's brand.json lives. Properties
  are looked up from ``https://{house_domain}/.well-known/brand.json`` at
  request time, so the salesagent doesn't cache or synchronize them.

- ``public_agent_url`` — what publishers list in their adagents.json to
  authorize this tenant's agent. For Scope3 managed-mode tenants this is
  ``https://interchange.io``; for self-hosted publishers it's their own
  salesagent's URL.

Both nullable in this migration — existing tenants get NULL defaults and
fall back to today's AuthorizedProperty cache. The Tenant Management API
makes both fields required for new managed-mode provisions; legacy create
remains permissive until the deprecation window closes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9f3e7a2b1c4"
down_revision: Union[str, Sequence[str], None] = "c8a5e1d3f4b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tenants",
        sa.Column("house_domain", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("public_agent_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenants", "public_agent_url")
    op.drop_column("tenants", "house_domain")
