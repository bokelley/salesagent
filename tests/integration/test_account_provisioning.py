"""Integration tests for sprint 1.6 piece C: Account → GAM advertiser resolution.

Covers:
- ``identity.account_id is None`` → returns None (legacy buyers fall through
  to Principal.platform_mappings; backward-compatible).
- Active Account with pre-mapped ``platform_mappings.google_ad_manager.advertiser_id``
  → returns it directly.
- Account in ``pending_provision`` + tenant.auto_provision_advertisers=True
  → calls GAM create_advertiser (stubbed in tests), persists id on the
  Account, flips status to active, returns id.
- Account in ``pending_provision`` + tenant.auto_provision_advertisers=False
  → raises ``AdCPAccountNotProvisioned``.
- Sandbox account → returns the per-tenant sandbox advertiser.
- Account row not found → returns None (let upstream ref-validation fail).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, AdapterConfig, Tenant
from src.core.helpers import account_provisioning
from src.core.helpers.account_provisioning import (
    AdCPAccountNotProvisioned,
    resolve_account_advertiser,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_tenant(*, auto_provision: bool = False, ad_server: str = "google_ad_manager") -> str:
    tid = f"t_prov_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.add(
            Tenant(
                tenant_id=tid,
                name=f"Provision Test {tid}",
                subdomain=tid,
                ad_server=ad_server,
                is_active=True,
                billing_plan="standard",
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_format_ids=[],
                policy_settings={},
                auto_provision_advertisers=auto_provision,
            )
        )
        session.add(AdapterConfig(tenant_id=tid, adapter_type="mock"))
        session.commit()
    return tid


def _make_account(
    tenant_id: str,
    *,
    status: str = "pending_provision",
    sandbox: bool = False,
    advertiser_id: str | None = None,
) -> str:
    aid = f"acct_{uuid.uuid4().hex[:8]}"
    platform_mappings: dict | None = None
    if advertiser_id is not None:
        platform_mappings = {"google_ad_manager": {"advertiser_id": advertiser_id}}
    with get_db_session() as session:
        session.add(
            Account(
                tenant_id=tenant_id,
                account_id=aid,
                name=f"Test Acct {aid}",
                status=status,
                operator="accuweather.com",
                brand={"domain": "cocacola.com"},
                billing="operator",
                sandbox=sandbox,
                platform_mappings=platform_mappings,
            )
        )
        session.commit()
    return aid


def _identity(tenant_id: str | None, account_id: str | None) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id="agent-A",
        tenant_id=tenant_id,
        account_id=account_id,
        tenant={"tenant_id": tenant_id, "ad_server": "google_ad_manager"} if tenant_id else None,
        protocol="mcp",
        testing_context=AdCPTestContext(),
    )


@pytest.fixture
def stub_gam_create(monkeypatch):
    """Stub the GAM CompanyService call. Returns the synthetic id used."""
    calls: list[str] = []

    def _stub(*, network_code, config, name, dry_run=False):
        calls.append(name)
        return f"stub_advertiser_{len(calls)}"

    monkeypatch.setattr(account_provisioning, "gam_create_advertiser_companyservice", _stub)
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolveAccountAdvertiser:
    def test_no_account_id_returns_none(self, integration_db):
        """Legacy buyers without ``account`` in the request → None.

        Caller falls back to Principal.platform_mappings (today's path).
        """
        identity = _identity(tenant_id=None, account_id=None)
        assert resolve_account_advertiser(identity) is None

    def test_active_account_with_advertiser_returns_it(self, integration_db, stub_gam_create):
        tid = _make_tenant()
        aid = _make_account(tid, status="active", advertiser_id="12345")
        identity = _identity(tenant_id=tid, account_id=aid)

        assert resolve_account_advertiser(identity) == "12345"
        assert stub_gam_create == []  # No GAM call needed — advertiser was mapped

    def test_pending_provision_with_auto_provision_creates_advertiser(self, integration_db, stub_gam_create):
        tid = _make_tenant(auto_provision=True)
        aid = _make_account(tid, status="pending_provision", advertiser_id=None)
        identity = _identity(tenant_id=tid, account_id=aid)

        # Adapter config probe — caller would normally pass this, but the
        # resolver also looks it up from AdapterConfig. With no AdapterConfig
        # row + no caller-supplied config, the resolver short-circuits to
        # AdCPAccountNotProvisioned ("no gam_network_code"). Pass the config
        # explicitly to exercise the success path.
        result = resolve_account_advertiser(identity, adapter_config={"network_code": "12345"})
        assert result == "stub_advertiser_1"
        assert stub_gam_create == ["accuweather.com × cocacola.com"]

        # Account row should now have status=active + advertiser_id stamped.
        with get_db_session() as session:
            account = session.scalars(select(Account).filter_by(account_id=aid)).first()
            assert account.status == "active"
            assert account.platform_mappings["google_ad_manager"]["advertiser_id"] == "stub_advertiser_1"
            assert account.platform_mappings["google_ad_manager"]["provisioned_by"] == "auto:create_media_buy"

    def test_pending_provision_without_auto_provision_raises(self, integration_db, stub_gam_create):
        tid = _make_tenant(auto_provision=False)
        aid = _make_account(tid, status="pending_provision", advertiser_id=None)
        identity = _identity(tenant_id=tid, account_id=aid)

        with pytest.raises(AdCPAccountNotProvisioned) as exc_info:
            resolve_account_advertiser(identity)
        assert "auto_provision_advertisers=False" in str(exc_info.value)
        assert exc_info.value.code == "ACCOUNT_NOT_PROVISIONED"
        # No GAM call attempted on the deny path.
        assert stub_gam_create == []

    def test_sandbox_account_returns_tenant_sandbox_advertiser(self, integration_db):
        """Sandbox accounts route to the tenant sandbox advertiser."""
        tid = _make_tenant(auto_provision=True)
        aid = _make_account(tid, status="active", sandbox=True, advertiser_id="should_be_ignored")
        identity = _identity(tenant_id=tid, account_id=aid)

        assert resolve_account_advertiser(identity) == f"sandbox-{tid}"
        with get_db_session() as session:
            account = session.scalars(select(Account).filter_by(account_id=aid)).first()
            assert account.platform_mappings["google_ad_manager"]["advertiser_id"] == f"sandbox-{tid}"
            assert account.platform_mappings["google_ad_manager"]["provisioned_by"] == "auto:sandbox"

    def test_account_row_not_found_returns_none(self, integration_db):
        """Buyer references an account_id that doesn't exist in this tenant
        → None. Upstream ref validation in the transport boundary is what
        actually surfaces this; here we just don't crash."""
        tid = _make_tenant()
        identity = _identity(tenant_id=tid, account_id="acct_does_not_exist")

        assert resolve_account_advertiser(identity) is None

    def test_billing_agent_advertiser_name_includes_principal(self, integration_db, stub_gam_create):
        tid = _make_tenant(auto_provision=True)
        aid = f"acct_{uuid.uuid4().hex[:8]}"
        with get_db_session() as session:
            session.add(
                Account(
                    tenant_id=tid,
                    account_id=aid,
                    name="Agent Billed",
                    status="pending_provision",
                    operator="accuweather.com",
                    brand={"domain": "cocacola.com"},
                    billing="agent",
                    sandbox=False,
                    principal_id="scope3-buyer",
                )
            )
            session.commit()
        identity = _identity(tenant_id=tid, account_id=aid)

        result = resolve_account_advertiser(identity, adapter_config={"network_code": "12345"})
        assert result == "stub_advertiser_1"
        # Name template embeds the buyer agent for billing=agent.
        assert stub_gam_create == ["accuweather.com × cocacola.com (scope3-buyer)"]
