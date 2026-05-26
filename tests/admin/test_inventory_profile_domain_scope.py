"""Tests for publisher-domain scope on inventory profiles."""

import pytest

from src.admin.blueprints.inventory_profiles import _bundle_authorable_domains
from tests.factories import PublisherPartnerFactory, TenantFactory

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


def test_bundle_authorable_domains_include_verified_publisher_partners(factory_session):
    tenant = TenantFactory()
    PublisherPartnerFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        publisher_domain="verified-publisher.example",
        is_verified=True,
    )
    PublisherPartnerFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        publisher_domain="pending-publisher.example",
        is_verified=False,
    )
    factory_session.commit()

    domains = _bundle_authorable_domains(factory_session, tenant.tenant_id, tenant)

    assert tenant.primary_domain in domains
    assert "verified-publisher.example" in domains
    assert "pending-publisher.example" not in domains
