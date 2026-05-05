"""Factory_boy factory for AuditLog model.

Sprint 3 of embedded-mode reads audit log rows via the management API. Tests
need to seed rows that exercise the action_prefix / subject / actor filters.
"""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import AuditLog
from tests.factories.core import TenantFactory


class AuditLogFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = AuditLog
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    operation = Sequence(lambda n: f"event.test_{n:04d}")
    success = True
    details = factory.LazyFunction(
        lambda: {
            "subject_type": "media_buy",
            "subject_id": "mb_test",
            "actor_type": "system",
        }
    )
