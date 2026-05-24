"""Helpers for publisher authorization integration test fixtures."""

from datetime import UTC, datetime

from src.core.database.models import PublisherPartner, Tenant
from tests.factories import AuthorizedPropertyFactory, PropertyTagFactory, PublisherPartnerFactory

DISCOVERED_TAG_DESCRIPTION = "Tag discovered from publisher adagents.json"


def seed_verified_publisher_authorization(
    tenant: Tenant,
    *,
    property_id: str,
    publisher_domain: str = "publisher.example",
) -> PublisherPartner:
    """Seed a verified publisher partner plus cached property/tag state."""
    partner = PublisherPartnerFactory(
        tenant=tenant,
        publisher_domain=publisher_domain,
        is_verified=True,
        last_synced_at=datetime.now(UTC),
        sync_status="success",
        total_properties=1,
        authorized_properties=1,
        aao_status_kind="authorized",
    )
    AuthorizedPropertyFactory(
        tenant=tenant,
        property_id=property_id,
        publisher_domain=publisher_domain,
    )
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory")
    PropertyTagFactory(
        tenant=tenant,
        tag_id="sports",
        description=DISCOVERED_TAG_DESCRIPTION,
    )
    return partner
