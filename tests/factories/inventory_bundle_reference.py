"""Factory_boy factory for InventoryBundleReference model."""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import InventoryBundleReference
from tests.factories.core import TenantFactory


class InventoryBundleReferenceFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = InventoryBundleReference
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    adapter = "gam"
    entity_type = "ad_unit"
    external_id = Sequence(lambda n: f"adunit_{n:06d}")
