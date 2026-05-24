"""Integration tests for TenantConfigRepository.

Verifies that the repository correctly queries PublisherPartner and AdapterConfig
models with tenant scoping against real PostgreSQL.

beads: salesagent-9y0
"""

import pytest

from src.core.database.models import AuthorizedProperty, PropertyTag
from src.core.database.repositories.tenant_config import TenantConfigRepository
from tests.factories import (
    AdapterConfigFactory,
    InventoryProfileFactory,
    ProductFactory,
    PublisherPartnerFactory,
    TenantFactory,
)
from tests.harness._base import IntegrationEnv
from tests.helpers.publisher_authorization import seed_verified_publisher_authorization

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests -- no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestListPublisherPartners:
    """list_publisher_partners returns all partners for the tenant."""

    def test_returns_all_partners(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tcr_test")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="alpha.com", display_name="Alpha")
            PublisherPartnerFactory(
                tenant=tenant,
                publisher_domain="beta.org",
                display_name="Beta",
                is_verified=False,
                sync_status="pending",
            )

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_test")
            partners = repo.list_publisher_partners()

        assert len(partners) == 2
        domains = {p.publisher_domain for p in partners}
        assert domains == {"alpha.com", "beta.org"}

    def test_tenant_isolation(self, integration_db):
        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="tcr_t1")
            t2 = TenantFactory(tenant_id="tcr_t2")
            PublisherPartnerFactory(tenant=t1, publisher_domain="t1.com")
            PublisherPartnerFactory(tenant=t2, publisher_domain="t2.com")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_t1")
            partners = repo.list_publisher_partners()

        domains = {p.publisher_domain for p in partners}
        assert domains == {"t1.com"}

    def test_empty_tenant(self, integration_db):
        with _RepoEnv() as env:
            session = env.get_session()
            repo = TenantConfigRepository(session, "nonexistent")
            partners = repo.list_publisher_partners()

        assert partners == []

    def test_invalidate_publisher_partner_aao_statuses(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(
                tenant_id="tcr_invalidate",
                is_embedded=False,
                public_agent_url="https://interchange.io/acme",
                virtual_host="agent-new.example.com",
            )
            partner = seed_verified_publisher_authorization(tenant, property_id="stale_property")
            profile = InventoryProfileFactory(
                tenant=tenant,
                publisher_properties=[
                    {"publisher_domain": "publisher.example", "property_tags": ["sports"], "selection_type": "by_tag"}
                ],
            )
            direct_product = ProductFactory(
                tenant=tenant,
                product_id="prod_direct_stale",
                properties=[{"publisher_domain": "publisher.example", "property_tags": ["sports"]}],
                property_tags=None,
            )
            profile_product = ProductFactory(
                tenant=tenant,
                product_id="prod_profile_stale",
                inventory_profile_id=profile.id,
            )
            other_tenant = TenantFactory(tenant_id="tcr_invalidate_other")
            seed_verified_publisher_authorization(
                other_tenant,
                property_id="other_property",
                publisher_domain="other.example",
            )

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_invalidate")
            repo.invalidate_publisher_partner_aao_statuses("Agent URL changed; refresh publisher authorization.")
            session.commit()
            session.refresh(partner)

            assert partner.is_verified is False
            assert partner.sync_status == "pending"
            assert partner.sync_error == "Agent URL changed; refresh publisher authorization."
            assert partner.total_properties is None
            assert partner.authorized_properties is None
            assert partner.aao_status_kind is None
            assert partner.last_synced_at is None
            assert partner.last_refreshed_at is None
            stale_property = session.get(
                AuthorizedProperty,
                {"tenant_id": "tcr_invalidate", "property_id": "stale_property"},
            )
            assert stale_property is not None
            assert stale_property.verification_status == "pending"
            assert stale_property.verification_error == "Agent URL changed; refresh publisher authorization."
            assert stale_property.verification_checked_at is None
            assert (
                session.get(
                    AuthorizedProperty,
                    {"tenant_id": "tcr_invalidate_other", "property_id": "other_property"},
                )
                is not None
            )
            assert session.get(PropertyTag, {"tenant_id": "tcr_invalidate", "tag_id": "sports"}) is None
            assert session.get(PropertyTag, {"tenant_id": "tcr_invalidate", "tag_id": "all_inventory"}) is not None
            assert session.get(PropertyTag, {"tenant_id": "tcr_invalidate_other", "tag_id": "sports"}) is not None
            session.refresh(profile)
            session.refresh(direct_product)
            session.refresh(profile_product)
            assert profile.publisher_properties == [
                {
                    "publisher_domain": "interchange.io",
                    "property_tags": ["all_inventory"],
                    "selection_type": "by_tag",
                }
            ]
            assert direct_product.inventory_profile_id is None
            assert direct_product.properties == [
                {
                    "publisher_domain": "interchange.io",
                    "property_tags": ["all_inventory"],
                    "selection_type": "by_tag",
                }
            ]
            assert direct_product.property_ids is None
            assert direct_product.property_tags is None
            assert profile_product.inventory_profile_id is None
            assert profile_product.properties == [
                {
                    "publisher_domain": "interchange.io",
                    "property_tags": ["all_inventory"],
                    "selection_type": "by_tag",
                }
            ]
            assert profile_product.property_ids is None
            assert profile_product.property_tags is None


class TestGetAdapterConfig:
    """get_adapter_config returns the adapter config row for the tenant."""

    def test_returns_config(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tcr_ac")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_ac")
            config = repo.get_adapter_config()

        assert config is not None
        assert config.adapter_type == "broadstreet"

    def test_returns_none_when_missing(self, integration_db):
        with _RepoEnv() as env:
            TenantFactory(tenant_id="tcr_no_config")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_no_config")
            config = repo.get_adapter_config()

        assert config is None


class TestListPublisherDomains:
    """list_publisher_domains returns sorted domain strings."""

    def test_sorted_domains(self, integration_db):
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tcr_dom")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="zebra.com")
            PublisherPartnerFactory(tenant=tenant, publisher_domain="alpha.com")

            session = env.get_session()
            repo = TenantConfigRepository(session, "tcr_dom")
            domains = repo.list_publisher_domains()

        assert domains == ["alpha.com", "zebra.com"]
