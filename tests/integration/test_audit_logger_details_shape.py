"""Regression test: log_security_violation must write details as a JSONB object.

Prior to the fix, ``log_security_violation`` passed
``details=json.dumps({...})`` into the JSONB column, double-encoding the
payload so it landed as a JSONB *string* rather than an object. Strict
readers (notably tenant_export) refused those rows. ~1,272 production rows
were corrupted before the bug was caught.

This test pins the contract: details round-trips as a dict.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from src.core.audit_logger import AuditLogger
from src.core.database.models import AuditLog
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _AuditEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}
    use_real_db = True

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestLogSecurityViolationDetailsShape:
    def test_details_persists_as_jsonb_object_not_string(self, integration_db):
        from tests.factories import TenantFactory

        with _AuditEnv() as env:
            session = env.get_session()
            TenantFactory(tenant_id="audit_shape_t1")
            session.commit()

            logger = AuditLogger(adapter_name="test_adapter", tenant_id="audit_shape_t1")
            logger.log_security_violation(
                operation="unauthorized_read",
                principal_id="buyer_x",
                resource_id="resource_42",
                reason="principal does not own resource",
            )

            row = session.scalars(
                select(AuditLog)
                .where(AuditLog.tenant_id == "audit_shape_t1")
                .where(AuditLog.operation.like("SECURITY_VIOLATION:%"))
            ).first()

            assert row is not None
            # ORM-level: JSONType.process_result_value should return a dict.
            assert isinstance(row.details, dict), (
                f"audit_logs.details must be a dict, got {type(row.details).__name__}: {row.details!r}"
            )
            assert row.details == {
                "resource_id": "resource_42",
                "reason": "principal does not own resource",
            }

            # Postgres-level: jsonb_typeof must be 'object', not 'string'.
            # This is what tenant_export's strict reader checks against.
            jsonb_type = session.execute(
                text("SELECT jsonb_typeof(details::jsonb) FROM audit_logs WHERE log_id = :log_id"),
                {"log_id": row.log_id},
            ).scalar()
            assert jsonb_type == "object", (
                f"jsonb_typeof(details) must be 'object', got {jsonb_type!r} — "
                "this is the regression that broke tenant exports"
            )
