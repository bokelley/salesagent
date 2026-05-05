"""Factory_boy factory for SyncJob model.

Sprint 3 of embedded-mode reads sync history rows via the management API.
"""

from __future__ import annotations

from datetime import UTC, datetime

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import SyncJob
from tests.factories.core import TenantFactory


class SyncJobFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = SyncJob
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    sync_id = Sequence(lambda n: f"sync_{n:08d}")
    adapter_type = "google_ad_manager"
    sync_type = "inventory"
    status = "completed"
    started_at = factory.LazyFunction(lambda: datetime.now(UTC))
    triggered_by = "test"
