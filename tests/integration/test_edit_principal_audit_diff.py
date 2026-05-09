"""Audit-log records before/after when an admin flips ``billing_enabled``.

BR-RULE-061 makes ``Principal.billing_enabled`` auth-relevant — it
gates which principals can use agent-billed accounts on
``sync_accounts``. Flips of this field need a clear paper trail so a
security reviewer can subpoena the change later and tie it to the
admin user who made it. Issue #33.
"""

from __future__ import annotations

import pytest
from sqlalchemy import desc, select

from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog, Principal

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.admin]

_SAME_ORIGIN_HEADERS = {"Origin": "http://localhost"}


def _seed_principal(tenant_id: str, principal_id: str, *, billing_enabled: bool) -> None:
    """Persist a Principal via factory-boy under the existing test tenant.

    PrincipalFactory has ``tenant = SubFactory(TenantFactory)`` so we
    must pass the existing Tenant ORM row explicitly; otherwise the
    SubFactory creates a fresh tenant with a different id and the
    Principal is orphaned from ``test_tenant_with_data``.
    """
    from src.core.database.models import Tenant
    from tests.factories import ALL_FACTORIES, PrincipalFactory

    with get_db_session() as session:
        try:
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = session
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            assert tenant is not None, f"test fixture didn't create tenant {tenant_id!r}"
            PrincipalFactory(
                tenant=tenant,
                principal_id=principal_id,
                billing_enabled=billing_enabled,
            )
        finally:
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = None


def _latest_edit_principal_audit(tenant_id: str) -> AuditLog | None:
    """The audit_logger prefixes operation with the adapter name, so the
    DB row's operation is ``AdminUI.edit_principal`` (see
    ``src/core/audit_logger.py:114``)."""
    with get_db_session() as session:
        return session.scalars(
            select(AuditLog)
            .filter_by(tenant_id=tenant_id, operation="AdminUI.edit_principal")
            .order_by(desc(AuditLog.timestamp))
            .limit(1)
        ).first()


def _post_edit(client, tenant_id: str, principal_id: str, **form_overrides) -> None:
    """Post the edit-principal form with sensible defaults so the route
    actually flows past validation."""
    form = {
        "name": "Test Advertiser",
        "agent_url": "https://buyer.example.com/agent",
        "brand_domain": "buyer.example.com",
        # Both flags default OFF in the form (HTML checkboxes — absent
        # means False); tests opt them in by passing the key.
    }
    form.update(form_overrides)
    client.post(
        f"/tenant/{tenant_id}/principals/{principal_id}/edit",
        data=form,
        headers=_SAME_ORIGIN_HEADERS,
        follow_redirects=False,
    )


class TestBillingEnabledAuditDiff:
    """edit_principal audit row records before/after on billing_enabled flips."""

    def test_flip_off_records_diff(self, authenticated_admin_session, test_tenant_with_data):
        tenant_id = test_tenant_with_data["tenant_id"]
        principal_id = "p_audit_off"
        _seed_principal(tenant_id, principal_id, billing_enabled=True)

        # Form omits billing_enabled (checkbox unchecked) → False after.
        _post_edit(authenticated_admin_session, tenant_id, principal_id)

        with get_db_session() as session:
            principal = session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            assert principal is not None
            assert principal.billing_enabled is False, "DB state should reflect the flip"

        audit = _latest_edit_principal_audit(tenant_id)
        assert audit is not None, "edit_principal audit row was not written"
        assert audit.details is not None
        assert audit.details.get("billing_enabled_before") is True, audit.details
        assert audit.details.get("billing_enabled_after") is False, audit.details

    def test_flip_on_records_diff(self, authenticated_admin_session, test_tenant_with_data):
        tenant_id = test_tenant_with_data["tenant_id"]
        principal_id = "p_audit_on"
        _seed_principal(tenant_id, principal_id, billing_enabled=False)

        # Checkbox checked → 'on' is the standard browser value.
        _post_edit(authenticated_admin_session, tenant_id, principal_id, billing_enabled="on")

        audit = _latest_edit_principal_audit(tenant_id)
        assert audit is not None
        assert audit.details is not None
        assert audit.details.get("billing_enabled_before") is False, audit.details
        assert audit.details.get("billing_enabled_after") is True, audit.details

    def test_no_flip_records_no_diff(self, authenticated_admin_session, test_tenant_with_data):
        """Edits that don't touch billing_enabled must NOT pollute the
        audit row with a fake before=after diff."""
        tenant_id = test_tenant_with_data["tenant_id"]
        principal_id = "p_audit_noop"
        _seed_principal(tenant_id, principal_id, billing_enabled=True)

        _post_edit(authenticated_admin_session, tenant_id, principal_id, billing_enabled="on")

        audit = _latest_edit_principal_audit(tenant_id)
        assert audit is not None
        details = audit.details or {}
        assert "billing_enabled_before" not in details, details
        assert "billing_enabled_after" not in details, details
