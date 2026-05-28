"""Regression coverage for tenant-scoped bearer/service token lookup."""

import pytest

from src.core.auth_utils import get_principal_from_token
from tests.factories import PrincipalFactory, TenantFactory
from tests.helpers.managed_tenant_api import bind_factories_to_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_tenant_scoped_service_token_rejected_after_tenant_delete(integration_db):
    """Inactive tenants must not accept their old principal access token."""
    with bind_factories_to_session():
        tenant = TenantFactory(
            tenant_id="inactive-token-tenant",
            subdomain="inactive-token-tenant",
            is_active=False,
        )
        PrincipalFactory(
            tenant=tenant,
            principal_id="inactive-buyer",
            access_token="inactive-service-token",
        )

    principal_id, tenant_context = get_principal_from_token(
        "inactive-service-token",
        tenant_id="inactive-token-tenant",
    )

    assert principal_id is None
    assert tenant_context is None


def test_tenant_scoped_service_token_still_accepts_active_tenant(integration_db):
    """The inactive-tenant guard must not break normal scoped lookup."""
    with bind_factories_to_session():
        tenant = TenantFactory(
            tenant_id="active-token-tenant",
            subdomain="active-token-tenant",
            is_active=True,
        )
        PrincipalFactory(
            tenant=tenant,
            principal_id="active-buyer",
            access_token="active-service-token",
        )

    principal_id, tenant_context = get_principal_from_token(
        "active-service-token",
        tenant_id="active-token-tenant",
    )

    assert principal_id == "active-buyer"
    assert tenant_context is None
