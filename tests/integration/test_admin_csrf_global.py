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
    """The before_request guard refuses cross-origin POSTs."""

    def test_post_with_no_origin_or_referer_is_403(self, production_admin_client):
        resp = production_admin_client.post("/tenant/anything/deactivate", follow_redirects=False)
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.data!r}"

    def test_post_with_evil_origin_is_403(self, production_admin_client):
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"Origin": "https://evil.example.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_post_with_evil_referer_is_403(self, production_admin_client):
        """Origin missing but Referer points elsewhere — still CSRF."""
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"Referer": "https://evil.example.com/attack.html"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_post_with_same_origin_passes_csrf_guard(self, production_admin_client):
        """A same-origin POST passes the CSRF guard — it may still 4xx
        downstream (auth, validation), but NOT 403 from the CSRF guard."""
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
        the upstream proxy, not the browser) — no cookie for an
        attacker's cross-origin POST to ride. The guard must yield."""
        resp = production_admin_client.post(
            "/tenant/anything/deactivate",
            headers={"X-Identity-Subject": "user@upstream.example.com"},
            follow_redirects=False,
        )
        assert resp.status_code != 403, (
            f"embedded-mode POST (X-Identity-Subject set) should not be CSRF-rejected; got 403: {resp.data!r}"
        )
