"""End-to-end role enforcement on tenant-scoped admin routes.

Sprint 4 / role enforcement contract test. Unit tests at
``tests/unit/test_role_normalization.py`` cover the primitives;
the structural guard at
``tests/unit/test_architecture_role_policy_declared.py`` ensures every
mutation declares a policy. This file proves the policy is actually
enforced — a member who tries an admin-only mutation gets 403, a
member who tries an admin+member mutation passes the role check, a
viewer is rejected from any mutation.

Test mode (``ADCP_AUTH_TEST_MODE=true``) drives auth without OAuth so
we can flip the role per test via ``session["test_user_role"]``. The
RBAC layer reads ``g.user["role"]`` regardless of auth path, so test-
mode coverage exercises the same gate code path as production.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.admin, pytest.mark.auth]


@pytest.fixture
def app(integration_db, monkeypatch):
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
    from src.admin.app import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


def _client_with_role(app, role: str, tenant_id: str = "any"):
    """Test client with a session populated for the given tenant role.

    ``role`` may be ``admin``, ``member``, ``viewer``, or ``super_admin``.
    The decorator's role gate reads the normalized canonical enum, so
    ``super_admin`` is the staff-bypass marker (becomes ``admin`` for
    role-check purposes).

    For non-``super_admin`` roles, ``test_tenant_id`` MUST match the
    ``tenant_id`` in the URL — otherwise the test-mode bypass in
    ``require_tenant_access`` falls through to the OAuth branch and we
    never reach the role gate.
    """
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": f"{role}@example.com", "name": role.title()}
        sess["test_user_role"] = role
        sess["test_tenant_id"] = "*" if role == "super_admin" else tenant_id
    return c


# Routes representative of each policy bucket. Picked for stability
# (these decorators are intentionally tagged in the sweep) and minimal
# side-effects on a real DB — the gate fires before the handler so the
# tenant doesn't need to exist for the role gate to reject.
ADMIN_ONLY_MUTATION = ("/tenant/any/users/add", "POST", {"email": "test@example.com", "role": "viewer"})
OPERATIONAL_MUTATION = ("/tenant/any/publisher-partners", "POST", {"publisher_domain": "example.com"})
READ_ROUTE = ("/tenant/any/users", "GET", None)


def _request(client, method: str, url: str, body):
    if body is None:
        return client.open(url, method=method)
    return client.open(url, method=method, json=body)


class TestAdminOnlyMutation:
    """``/tenant/<id>/users/add`` is declared ``role=("admin",)``."""

    def test_admin_passes_role_gate(self, app):
        url, method, body = ADMIN_ONLY_MUTATION
        resp = _request(_client_with_role(app, "super_admin"), method, url, body)
        # Test super_admin maps to admin role; passes the role gate.
        # Handler then 404s on missing tenant or proceeds — anything BUT
        # 403 means the gate passed.
        assert (
            resp.status_code != 403
        ), f"admin should clear admin-only role gate; got 403: {resp.get_data(as_text=True)[:200]}"

    def test_member_blocked_with_role_not_authorized(self, app):
        url, method, body = ADMIN_ONLY_MUTATION
        resp = _request(_client_with_role(app, "member"), method, url, body)
        assert (
            resp.status_code == 403
        ), f"member must be blocked from admin-only route: {resp.get_data(as_text=True)[:200]}"

    def test_viewer_blocked(self, app):
        url, method, body = ADMIN_ONLY_MUTATION
        resp = _request(_client_with_role(app, "viewer"), method, url, body)
        assert resp.status_code == 403


class TestOperationalMutation:
    """``/tenant/<id>/publisher-partners`` is ``role=("admin", "member")``."""

    def test_admin_passes(self, app):
        url, method, body = OPERATIONAL_MUTATION
        resp = _request(_client_with_role(app, "super_admin"), method, url, body)
        assert resp.status_code != 403, resp.get_data(as_text=True)[:200]

    def test_member_passes(self, app):
        url, method, body = OPERATIONAL_MUTATION
        resp = _request(_client_with_role(app, "member"), method, url, body)
        # Member should clear the role gate. Handler may 404/500 on a
        # missing tenant — anything but 403 with the role error counts
        # as the role gate passing.
        body_text = resp.get_data(as_text=True)
        assert (
            resp.status_code != 403
            or "embedded_writes_not_permitted" in body_text
            or ("role_not_authorized" not in body_text)
        ), f"member should pass admin+member role gate; got {resp.status_code}: {body_text[:200]}"

    def test_viewer_blocked_with_role_not_authorized(self, app):
        url, method, body = OPERATIONAL_MUTATION
        resp = _request(_client_with_role(app, "viewer"), method, url, body)
        assert resp.status_code == 403
        body_text = resp.get_data(as_text=True)
        assert "role_not_authorized" in body_text or "Role" in body_text


class TestReadRoute:
    """GETs on tenant-scoped routes don't get a role gate by default,
    so any authenticated session — admin, member, or viewer — passes."""

    @pytest.mark.parametrize("role", ["super_admin", "member", "viewer"])
    def test_any_role_passes_read(self, app, role):
        url, method, body = READ_ROUTE
        resp = _request(_client_with_role(app, role), method, url, body)
        assert resp.status_code != 403, (
            f"role {role!r} blocked on read route — role gate fired on a GET? "
            f"got {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
        )
