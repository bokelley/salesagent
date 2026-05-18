"""Integration tests for account resolution error codes in create_media_buy context.

Verifies that account resolution errors return spec-compliant error codes
(ACCOUNT_NOT_FOUND, ACCOUNT_AMBIGUOUS) rather than generic codes (NOT_FOUND).

beads: salesagent-2rq
"""

import pytest
from adcp.types.generated_poc.core.account_ref import (
    AccountReference,
    AccountReference1,
    AccountReference2,
)
from adcp.types.generated_poc.core.brand_ref import BrandReference

from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPAccountNotFoundError, AdCPNotFoundError
from src.core.helpers.account_helpers import resolve_account
from src.core.resolved_identity import ResolvedIdentity
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _AccountResolutionEnv(IntegrationEnv):
    """Bare integration env for account resolution tests."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


def _make_identity(tenant_id: str, principal_id: str = "agent_001") -> ResolvedIdentity:
    return ResolvedIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        auth_token="test-token",
    )


class TestAccountResolutionErrorCodes:
    """Account resolution errors must use spec-compliant error codes."""

    def test_not_found_by_id_returns_account_not_found(self, integration_db):
        """resolve_account with nonexistent account_id → ACCOUNT_NOT_FOUND."""
        from tests.factories import TenantFactory

        with _AccountResolutionEnv() as env:
            tenant = TenantFactory(tenant_id="acct_err_t1")
            env.get_session()  # commit factory data

            identity = _make_identity(tenant.tenant_id)
            ref = AccountReference(root=AccountReference1(account_id="nonexistent_acc"))

            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPAccountNotFoundError) as exc_info:
                    resolve_account(ref, identity, uow.accounts)

            # AdCPAccountNotFoundError is a subclass of AdCPNotFoundError (still 404)
            assert isinstance(exc_info.value, AdCPNotFoundError)
            assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"

    def test_not_found_by_natural_key_on_unactivated_tenant_raises_tenant_not_activated(self, integration_db):
        """Sprint 1.8 cutover: resolve_account with a nonexistent natural
        key on a tenant lacking default_gam_advertiser_id and routing
        rules raises ``TENANT_NOT_ACTIVATED`` from the routing chain
        rather than the legacy ``AccountNotFoundError``.

        Buyer-protocol error path IS the activation contract.
        """
        from src.services.buyer_advertiser_routing import AdCPTenantNotActivated
        from tests.factories import TenantFactory

        with _AccountResolutionEnv() as env:
            tenant = TenantFactory(tenant_id="acct_err_t2")
            env.get_session()

            identity = _make_identity(tenant.tenant_id)
            ref = AccountReference(
                root=AccountReference2(
                    brand=BrandReference(domain="nonexistent.com"),
                    operator="nobody.com",
                )
            )

            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPTenantNotActivated) as exc_info:
                    resolve_account(ref, identity, uow.accounts)

            assert exc_info.value.code == "TENANT_NOT_ACTIVATED"

    def test_natural_key_auto_creates_account_when_default_advertiser_set(self, integration_db):
        """Sprint 1.8 cutover happy path: tenant with default_gam_advertiser_id
        set → first buy with an unmapped (operator, brand) triple auto-creates
        an Account stamped with the resolved advertiser, and the resolver
        returns that Account's id (no AccountNotFoundError, no TENANT_NOT_ACTIVATED).
        """
        from tests.factories import PrincipalFactory, TenantFactory

        with _AccountResolutionEnv() as env:
            tenant = TenantFactory(
                tenant_id="acct_auto_create",
                default_gam_advertiser_id="default_adv_99",
            )
            principal = PrincipalFactory(tenant=tenant, principal_id="agent_001")
            env.get_session()

            identity = _make_identity(tenant.tenant_id, principal_id=principal.principal_id)
            ref = AccountReference(
                root=AccountReference2(
                    brand=BrandReference(domain="brand-new.example"),
                    operator="agent.example",
                )
            )

            with AccountUoW(tenant.tenant_id) as uow:
                account_id = resolve_account(ref, identity, uow.accounts)

            assert account_id.startswith("acct_")
            # Roundtrip: a second resolve hits the natural-key fast path
            # and returns the same id (no second auto-create).
            with AccountUoW(tenant.tenant_id) as uow:
                second_id = resolve_account(ref, identity, uow.accounts)
            assert second_id == account_id
