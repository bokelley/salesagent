"""Publisher partner property-detail regressions."""

from unittest.mock import AsyncMock, patch

import pytest

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
