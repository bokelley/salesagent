"""Publisher partner property-detail regressions."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select

from src.core.database.models import PublisherPartner
from tests.helpers.adagents import publisher_properties_dict_adagents

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_get_publisher_properties_denies_malformed_dict_selector(authenticated_admin_session, factory_session):
    """The detail endpoint must use the same fail-closed resolver as AAO sync."""
    from tests.factories import PublisherPartnerFactory, TenantFactory

    tenant = TenantFactory(
        tenant_id="tenant_partner_props_malformed",
        subdomain="tenant-partner-props-malformed",
        virtual_host="interchange.io",
        public_agent_url="https://interchange.io",
    )
    partner = PublisherPartnerFactory(
        tenant=tenant,
        publisher_domain="cafemedia.com",
        display_name="CafeMedia",
    )
    factory_session.commit()

    adagents = publisher_properties_dict_adagents()
    adagents["authorized_agents"][0]["publisher_properties"]["publisher_domain"] = "a.example.com"

    with patch(
        "src.admin.blueprints.publisher_partners.fetch_adagents",
        AsyncMock(return_value=adagents),
    ):
        response = authenticated_admin_session.get(
            f"/tenant/{tenant.tenant_id}/publisher-partners/{partner.id}/properties"
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {
        "error": "Agent https://interchange.io is not authorized by this publisher",
        "is_authorized": False,
    }


def test_lookup_publisher_properties_returns_domain_structure(authenticated_admin_session, factory_session):
    """Domain-first lookup upserts status and returns cached property IDs/tags."""
    from src.services.aao_lookup_service import PublisherPartnerStatus
    from tests.factories import AuthorizedPropertyFactory, TenantFactory

    tenant = TenantFactory(public_agent_url="https://interchange.io")
    AuthorizedPropertyFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        publisher_domain="espn.com",
        property_id="espn_home",
        name="ESPN Home",
        tags=["all_inventory", "sports"],
    )
    factory_session.commit()

    discovery = Mock()
    discovery.sync_properties_from_adagents_sync.return_value = {
        "domains_synced": 1,
        "properties_found": 1,
        "tags_found": 2,
        "errors": [],
        "dry_run": False,
    }
    status = PublisherPartnerStatus(
        publisher_domain="espn.com",
        total_properties=1,
        authorized_properties=1,
        status="authorized",
        aao_onboarding_url="https://agenticadvertising.org/publisher/espn.com",
        error=None,
    )

    with patch(
        "src.admin.blueprints.publisher_partners.get_publisher_partner_status",
        new=AsyncMock(return_value=status),
    ) as status_lookup:
        with patch("src.admin.blueprints.publisher_partners.check_url_ssrf", return_value=(True, "")):
            with patch(
                "src.services.property_discovery_service.get_property_discovery_service",
                return_value=discovery,
            ):
                response = authenticated_admin_session.post(
                    f"/tenant/{tenant.tenant_id}/publisher-properties/lookup",
                    json={"publisher_domain": "https://ESPN.com/"},
                )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["publisher_domain"] == "espn.com"
    assert body["is_authorized"] is True
    assert body["aao_status"] == "authorized"
    assert body["property_ids"] == ["espn_home"]
    assert body["property_tags"] == ["all_inventory", "sports"]
    assert body["properties"][0]["name"] == "ESPN Home"
    status_lookup.assert_awaited_once_with("espn.com", "https://interchange.io", force_refresh=False)
    discovery.sync_properties_from_adagents_sync.assert_called_once_with(
        tenant.tenant_id,
        publisher_domains=["espn.com"],
        dry_run=False,
        agent_url="https://interchange.io",
    )

    factory_session.expire_all()
    partner = factory_session.scalars(
        select(PublisherPartner).filter_by(tenant_id=tenant.tenant_id, publisher_domain="espn.com")
    ).first()
    assert partner is not None
    assert partner.is_verified is True
    assert partner.aao_status_kind == "authorized"
