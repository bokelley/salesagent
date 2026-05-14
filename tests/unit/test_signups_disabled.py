"""Tests for the ALLOW_SIGNUPS cluster-wide kill switch.

When ALLOW_SIGNUPS=false the four /signup* routes must not provision new tenants.
Used to close registrations on the hosted cluster while existing tenants are migrated.
"""

import os
from unittest.mock import patch


class TestSignupsDisabled:
    def test_landing_renders_closed_page_when_disabled(self, admin_app):
        with patch.dict("os.environ", {"ALLOW_SIGNUPS": "false"}):
            with admin_app.test_client() as client:
                resp = client.get("/signup")
                assert resp.status_code == 503
                assert b"closed" in resp.data.lower()

    def test_signup_start_redirects_to_landing_when_disabled(self, admin_app):
        with patch.dict("os.environ", {"ALLOW_SIGNUPS": "false"}):
            with admin_app.test_client() as client:
                resp = client.get("/signup/start", follow_redirects=False)
                assert resp.status_code == 302
                assert "/signup" in resp.headers["Location"]
                with client.session_transaction() as sess:
                    assert "signup_flow" not in sess

    def test_provision_redirects_when_disabled(self, admin_app):
        with patch.dict("os.environ", {"ALLOW_SIGNUPS": "false"}):
            with admin_app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["signup_flow"] = True
                    sess["user"] = "x@example.com"
                resp = client.post(
                    "/signup/provision",
                    data={"publisher_name": "Acme", "adapter": "mock"},
                    follow_redirects=False,
                )
                assert resp.status_code == 302
                assert "/signup" in resp.headers["Location"]

    def test_landing_works_when_enabled(self, admin_app):
        with patch.dict("os.environ", {"ALLOW_SIGNUPS": "true"}):
            with admin_app.test_client() as client:
                resp = client.get("/signup")
                assert resp.status_code != 503

    def test_default_is_enabled(self):
        """Absence of ALLOW_SIGNUPS env var must leave signups open (backwards compatible)."""
        from src.admin.blueprints.public import signups_enabled

        os.environ.pop("ALLOW_SIGNUPS", None)
        assert signups_enabled() is True
