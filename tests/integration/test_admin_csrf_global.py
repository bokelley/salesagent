"""Global CSRF defense for the admin Flask app.

Refuses mutating cookie-authed POSTs from third-party origins. The
session cookie is ``SameSite=None`` for OAuth flow reasons, so the
cookie rides cross-origin POSTs — only the Origin/Referer comparison
reliably distinguishes a legit admin form submission from a CSRF
attack on ``evil.example.com``.

This guard is on top of the per-route Origin checks the signing-keys
admin shipped with (PR #234) — defense in depth, plus closes #32 for
every other admin POST that didn't have its own check.

Tests run with ``TESTING=False`` to exercise the production path —
the default conftest fixture sets ``TESTING=True`` (which bypasses
the guard so legacy tests don't break).
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.admin]


@pytest.fixture
def production_admin_client(integration_db):
    """Admin app with TESTING=False so the CSRF before_request fires."""
    app = create_app()
    app.config["TESTING"] = False
    app.config["WTF_CSRF_ENABLED"] = False  # Inert here, but keep parity.
    app.config["SESSION_COOKIE_PATH"] = "/"
    app.config["SESSION_COOKIE_HTTPONLY"] = False
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as client:
        yield client


class TestAdminCsrfGlobal:
    """The before_request guard refuses cross-origin POSTs.

    CSRF is only a threat when a session cookie can be auto-attached
    by the victim's browser, so the cookie-authed tests all set the
    session cookie explicitly to establish that precondition.
    Cookieless POSTs are not CSRF and are covered by the structural
    bypass tests further down.
    """

    @staticmethod
    def _attach_session_cookie(client) -> None:
        """Simulate a logged-in browser by setting the Flask session
        cookie. Value content doesn't matter for the CSRF guard — its
        presence is what makes cross-origin POSTs exploitable.

        Flask 3.x signature: ``set_cookie(key, value=, **kwargs)``."""
        client.set_cookie("session", "fakevalue", domain="localhost")

    def test_post_with_session_and_no_origin_or_referer_is_403(self, production_admin_client):
        """Cookie present + no Origin/Referer is the classic CSRF shape
        (auto-submitted form from an attacker page)."""
        self._attach_session_cookie(production_admin_client)
        resp = production_admin_client.post("/tenant/anything/deactivate", follow_redirects=False)
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.data!r}"

    def test_post_with_session_and_evil_origin_is_403(self, production_admin_client):
        self._attach_session_cookie(production_admin_client)
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"Origin": "https://evil.example.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_post_with_session_and_evil_referer_is_403(self, production_admin_client):
        """Origin missing but Referer points elsewhere — still CSRF."""
        self._attach_session_cookie(production_admin_client)
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"Referer": "https://evil.example.com/attack.html"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_post_with_session_and_same_origin_passes_csrf_guard(self, production_admin_client):
        """A same-origin cookie-authed POST passes the CSRF guard — it
        may still 4xx downstream (auth, validation), but NOT 403 from
        the CSRF guard."""
        self._attach_session_cookie(production_admin_client)
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"Origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code != 403, f"same-origin POST should not be CSRF-rejected; got 403: {resp.data!r}"

    def test_get_is_never_csrf_rejected(self, production_admin_client):
        """Read methods don't change state, so the CSRF guard must not
        intervene even with no Origin header."""
        resp = production_admin_client.get("/tenant/anything", follow_redirects=False)
        assert resp.status_code != 403

    def test_embedded_mode_post_bypasses_csrf(self, production_admin_client):
        """Embedded-mode requests authenticate via X-Identity-* (set by
        the upstream proxy, not the browser). Even if the proxy forwards
        a session cookie, the explicit identity header signals the guard
        to yield — the request is not cookie-authed against this app."""
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"X-Identity-Subject": "user@upstream.example.com"},
            follow_redirects=False,
        )
        assert resp.status_code != 403, (
            f"embedded-mode POST (X-Identity-Subject set) should not be CSRF-rejected; got 403: {resp.data!r}"
        )

    def test_cookieless_post_bypasses_csrf(self, production_admin_client):
        """The structural rule: no session cookie → no CSRF possible.
        A POST with no cookies at all must not be 403'd by the CSRF
        guard regardless of path — per-route auth still applies.

        Note: ``FlaskClient`` persists cookies across requests in its
        own cookie jar; this test relies on the per-test fixture
        yielding a fresh client (so no prior session cookie carries
        over), not on the client being stateless."""
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            follow_redirects=False,
        )
        assert resp.status_code != 403, f"cookieless POST should not be CSRF-rejected; got 403: {resp.data!r}"

    def test_tenant_management_api_s2s_post_bypasses_csrf(self, production_admin_client):
        """Regression for the original symptom: agentic-api POSTing to
        /api/v1/tenant-management/tenants/provision with only an API
        key header and no cookies was being 403'd as cross-origin.
        The structural bypass (no session cookie) must let it through
        to the per-route @require_api_key_auth decorator."""
        resp = production_admin_client.post(
            "/api/v1/tenant-management/tenants/provision",
            headers={"X-Tenant-Management-API-Key": "irrelevant-for-csrf"},
            json={},
            follow_redirects=False,
        )
        assert resp.status_code != 403, (
            f"tenant-management S2S POST should not be CSRF-rejected; got 403: {resp.data!r}"
        )

    def test_sync_api_s2s_post_bypasses_csrf(self, production_admin_client):
        """Same structural guarantee for /api/sync/* (mounted at
        /api/sync, not /api/v1/sync — see register_blueprint call)."""
        resp = production_admin_client.post(
            "/api/sync/trigger/some-tenant",
            headers={"X-API-Key": "irrelevant-for-csrf"},
            json={},
            follow_redirects=False,
        )
        assert resp.status_code != 403, f"sync S2S POST should not be CSRF-rejected; got 403: {resp.data!r}"
