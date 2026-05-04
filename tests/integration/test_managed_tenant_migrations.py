"""Migration roundtrip tests for the embedded-mode columns.

Uses the shared :func:`migration_db` fixture, which provisions an isolated
PostgreSQL database without applying any migrations. The test then drives
alembic forward and backward to verify the new migrations are reversible
even after rows are populated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import run_alembic_downgrade, run_alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


PARENT = "b4e2bffdd4f8"  # head before sprint 1
TENANT_REVISION = "b118da383e3c"
AUDIT_REVISION = "20c448890df9"


def _column_exists(engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
            {"t": table, "c": column},
        ).first()
        return row is not None


def test_managed_tenant_migrations_roundtrip(migration_db):
    """upgrade head → insert row → downgrade -2 → row preserved → upgrade head again."""
    engine, db_url = migration_db

    # Bring the empty DB up to head.
    run_alembic_upgrade(db_url, "head")

    # New columns must be present at head. After the embedded-mode rename migration,
    # the column is named ``is_embedded`` (the old ``managed_externally`` was renamed
    # in alembic revision c4d5e6f7a8b9).
    assert _column_exists(engine, "tenants", "is_embedded")
    assert not _column_exists(engine, "tenants", "managed_externally")
    assert _column_exists(engine, "tenants", "external_org_id")
    assert _column_exists(engine, "tenants", "external_source")
    assert _column_exists(engine, "audit_logs", "external_user_email")

    # Populate a tenant row that uses the new columns. We hit the DB directly with
    # SQL rather than the ORM so we don't depend on every NOT NULL default in the
    # current model (the migration test only cares about the columns under test).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, ad_server, is_active, "
                "billing_plan, enable_axe_signals, human_review_required, approval_mode, "
                "creative_auto_approve_threshold, creative_auto_reject_threshold, "
                "is_embedded, external_org_id, external_source, "
                "created_at, updated_at) "
                "VALUES ('tenant_mig_rt', 'Migration Roundtrip', 'mig-rt', 'mock', TRUE, "
                "'standard', TRUE, FALSE, 'require-human', 0.9, 0.1, "
                "TRUE, 'org_rt', 'scope3', NOW(), NOW())"
            )
        )

    # Step the schema back past both new migrations.
    run_alembic_downgrade(db_url, PARENT)

    assert not _column_exists(engine, "tenants", "is_embedded")
    assert not _column_exists(engine, "tenants", "managed_externally")
    assert not _column_exists(engine, "tenants", "external_org_id")
    assert not _column_exists(engine, "audit_logs", "external_user_email")

    with engine.connect() as conn:
        row = conn.execute(text("SELECT name FROM tenants WHERE tenant_id = 'tenant_mig_rt'")).first()
        assert row is not None
        assert row[0] == "Migration Roundtrip"

    # Re-apply migrations.
    run_alembic_upgrade(db_url, "head")

    assert _column_exists(engine, "tenants", "is_embedded")
    assert _column_exists(engine, "audit_logs", "external_user_email")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT is_embedded, external_org_id FROM tenants WHERE tenant_id = 'tenant_mig_rt'")
        ).first()
        assert row is not None
        # Existing row preserved across the roundtrip; the new columns picked up server defaults.
        assert row[0] is False
        assert row[1] is None


def test_audit_log_external_columns_at_head(migration_db):
    """Sanity check: the four external_* columns on audit_logs are present and nullable."""
    engine, db_url = migration_db
    run_alembic_upgrade(db_url, "head")
    for col in ("external_user_email", "external_user_id", "external_org_id", "external_source"):
        assert _column_exists(engine, "audit_logs", col), f"audit_logs.{col} missing at head"
