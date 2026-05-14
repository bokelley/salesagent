"""Integration tests for admin application."""

from unittest.mock import Mock, patch

import pytest

from src.admin.app import create_app


class TestAdminAppIntegration:
    """Integration tests for the admin Flask application."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        config = {
            "TESTING": True,
            "SECRET_KEY": "test_secret_key",
            "WTF_CSRF_ENABLED": False,  # Disable CSRF for testing
        }
        app = create_app(config)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    @pytest.fixture
    def authenticated_client(self, app):
        """Create authenticated test client."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "admin@example.com"
        return client

    def test_app_creation(self, app):
        """Test that app is created successfully."""
        assert app is not None
        assert app.config["TESTING"]
        assert app.secret_key == "test_secret_key"

    def test_blueprints_registered(self, app):
        """Test that all blueprints are registered."""
        blueprints = list(app.blueprints.keys())
        assert "auth" in blueprints
        assert "tenants" in blueprints
        assert "products" in blueprints

    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_index_requires_auth(self, client):
        """Test that index page requires authentication."""
        response = client.get("/")
        assert response.status_code == 302
        assert "/login" in response.location

    @patch("src.admin.utils.is_super_admin")
    @patch("src.admin.utils.get_db_session")
    def test_index_with_super_admin(self, mock_get_db_session, mock_is_super_admin, authenticated_client):
        """Test index page for super admin."""
        mock_is_super_admin.return_value = True

        # Mock database session
        mock_session = Mock()
        mock_get_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.order_by.return_value.all.return_value = []

        response = authenticated_client.get("/")
        assert response.status_code == 200

    def test_login_page_accessible(self, client):
        """Test that login page is accessible without auth."""
        response = client.get("/login")
        assert response.status_code == 200

    def test_context_processor_uses_request_script_root(self, app):
        """Template prefixing should follow request context rather than env mode."""
        with app.test_request_context("/tenant/test_tenant", environ_overrides={"SCRIPT_NAME": "/admin"}):
            context = {}
            for processor in app.template_context_processors[None]:
                context.update(processor())

        assert context["script_name"] == "/admin"

    def test_tenant_login_page(self, client):
        """Test tenant-specific login page."""
        # Need to patch in the auth blueprint where it's actually used
        with patch("src.admin.blueprints.auth.get_db_session") as mock_get_db_session:
            mock_session = Mock()
            mock_get_db_session.return_value.__enter__.return_value = mock_session

            # Mock tenant exists
            mock_tenant = Mock()
            mock_tenant.name = "Test Tenant"
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_tenant

            response = client.get("/tenant/test_tenant/login")
            assert response.status_code == 200

    def test_tenant_login_page_not_found(self, client):
        """Test tenant login page for non-existent tenant."""
        with patch("src.admin.blueprints.auth.get_db_session") as mock_get_db_session:
            mock_session = Mock()
            mock_get_db_session.return_value.__enter__.return_value = mock_session
            mock_session.query.return_value.filter_by.return_value.first.return_value = None

            response = client.get("/tenant/nonexistent/login")
            assert response.status_code == 404

    def test_logout_functionality(self, authenticated_client):
        """Test logout clears session."""
        # Verify authenticated
        with authenticated_client.session_transaction() as sess:
            assert sess.get("user") == "admin@example.com"

        # Logout
        response = authenticated_client.get("/logout")
        assert response.status_code == 302

        # Verify session cleared
        with authenticated_client.session_transaction() as sess:
            assert "user" not in sess

    @patch("src.admin.utils.is_super_admin")
    def test_settings_page_admin_only(self, mock_is_super_admin, authenticated_client):
        """Test that settings page is admin only."""
        # Non-admin should get 403
        mock_is_super_admin.return_value = False
        response = authenticated_client.get("/settings")
        assert response.status_code == 403

        # Admin should get 200
        mock_is_super_admin.return_value = True
        response = authenticated_client.get("/settings")
        assert response.status_code == 200

    def test_test_auth_disabled_by_default(self, client):
        """Test that test auth endpoints are disabled by default."""
        response = client.post("/test/auth", data={"email": "test@example.com", "password": "test123"})
        assert response.status_code == 404

    def test_test_auth_enabled_with_env_var(self, app):
        """Test that test auth works when enabled."""
        with patch.dict("os.environ", {"ADCP_AUTH_TEST_MODE": "true"}):
            client = app.test_client()

            response = client.post("/test/auth", data={"email": "test_super_admin@example.com", "password": "test123"})
            assert response.status_code == 302  # Redirect after login

            with client.session_transaction() as sess:
                assert sess.get("user") == "test_super_admin@example.com"


class TestTenantBlueprintIntegration:
    """Integration tests for tenant blueprint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        app = create_app({"TESTING": True})
        return app

    @pytest.fixture
    def client(self, app):
        """Create authenticated test client."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "admin@example.com"
        return client

    @patch("src.admin.utils.require_tenant_access")
    @patch("src.admin.utils.get_db_session")
    def test_tenant_dashboard(self, mock_get_db_session, mock_require_tenant_access, client):
        """Test tenant dashboard page."""
        # Mock decorator to allow access
        mock_require_tenant_access.return_value = lambda f: f

        # Mock database
        mock_session = Mock()
        mock_get_db_session.return_value.__enter__.return_value = mock_session

        mock_tenant = Mock()
        mock_tenant.name = "Test Tenant"
        mock_tenant.tenant_id = "tenant_123"
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_tenant
        mock_session.query.return_value.filter_by.return_value.count.return_value = 0
        mock_session.query.return_value.filter_by.return_value.filter.return_value.all.return_value = []
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        response = client.get("/tenant/tenant_123")
        # Will redirect due to decorator, but shows route exists
        assert response.status_code in [200, 302]


class TestProductsBlueprintIntegration:
    """Integration tests for products blueprint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        app = create_app({"TESTING": True})
        return app

    @pytest.fixture
    def client(self, app):
        """Create authenticated test client."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "admin@example.com"
        return client

    def test_products_routes_exist(self, app):
        """Test that product routes are registered."""
        rules = [str(rule) for rule in app.url_map.iter_rules()]

        # Check key product routes
        assert any("/tenant/<tenant_id>/products" in rule for rule in rules)
        assert any("/tenant/<tenant_id>/products/add" in rule for rule in rules)
        assert any("/tenant/<tenant_id>/products/<product_id>/edit" in rule for rule in rules)


class TestUncaughtExceptionHandler:
    """The 500 handler must log the traceback + request context and return
    a response with a stable error_id, so production crashes are diagnoseable
    instead of bottoming out in the upstream proxy's generic error page.
    """

    @pytest.fixture
    def app(self):
        # PROPAGATE_EXCEPTIONS=False forces Flask to invoke registered error
        # handlers for uncaught exceptions; TESTING=True would re-raise instead.
        app = create_app({"TESTING": True, "PROPAGATE_EXCEPTIONS": False})

        @app.route("/__boom_anonymous")
        def boom_anonymous():
            raise RuntimeError("synthetic crash for handler test")

        @app.route("/tenant/<tenant_id>/__boom_scoped")
        def boom_scoped(tenant_id):
            raise ValueError(f"tenant-scoped crash for {tenant_id}")

        return app

    def test_uncaught_exception_returns_500_with_error_id(self, app, caplog):
        client = app.test_client()
        with caplog.at_level("ERROR", logger="src.admin.app"):
            resp = client.get("/__boom_anonymous")

        assert resp.status_code == 500
        body = resp.get_data(as_text=True)
        assert "Error ID:" in body
        assert "Internal Server Error" in body

        # The logged record must include the traceback + request context.
        handler_records = [r for r in caplog.records if "Uncaught exception in GET" in r.getMessage()]
        assert handler_records, f"expected handler log; got: {[r.getMessage() for r in caplog.records]}"
        rec = handler_records[0]
        msg = rec.getMessage()
        assert "/__boom_anonymous" in msg
        # Sanitizer always returns a string, so a Python ``None`` tenant_id
        # is logged as the literal ``'None'`` (quoted via ``%r``). The
        # ``=None`` substring would falsely also match ``='None'``; pin
        # the exact post-sanitize shape.
        assert "tenant_id='None'" in msg
        assert rec.exc_info is not None, "exc_info must be attached so the traceback is logged"

    def test_handler_captures_tenant_id_from_view_args(self, app, caplog):
        client = app.test_client()
        with caplog.at_level("ERROR", logger="src.admin.app"):
            resp = client.get("/tenant/acme/__boom_scoped")
        assert resp.status_code == 500
        handler_records = [r for r in caplog.records if "Uncaught exception in GET" in r.getMessage()]
        assert handler_records
        msg = handler_records[0].getMessage()
        assert "tenant_id='acme'" in msg, msg

    def test_handler_returns_json_when_requested(self, app):
        client = app.test_client()
        resp = client.get("/__boom_anonymous", headers={"Accept": "application/json"})
        assert resp.status_code == 500
        assert resp.is_json
        body = resp.get_json()
        assert body["error"] == "internal_server_error"
        assert "error_id" in body and len(body["error_id"]) >= 6

    def test_http_exceptions_pass_through(self, app):
        """Werkzeug HTTPExceptions (404, 403, etc.) must NOT be wrapped —
        those are intentional responses, not internal errors."""
        client = app.test_client()
        resp = client.get("/this-route-does-not-exist")
        assert resp.status_code == 404
        # 404 page is Werkzeug's default, not our 500 handler's body.
        assert "Error ID:" not in resp.get_data(as_text=True)

    def test_handler_sanitizes_attacker_controlled_email(self, app, caplog):
        """The session ``user`` lookup pulls from ``X-Identity-Email`` in
        embedded mode — that header is attacker-controllable per the
        contract (the upstream proxy IS expected to sanitize, but defense
        in depth). A value with embedded CR/LF must NOT forge a second
        log line in the access aggregator."""
        client = app.test_client()

        # Synthesize an embedded session by writing g/session ourselves
        # via a custom view that crashes after the session is set.
        @app.route("/__boom_injected_email")
        def boom_injected_email():
            from flask import session as flask_session

            flask_session["user"] = {"email": "attacker@evil.com\n[CRIT] forged log line"}
            raise RuntimeError("synthetic crash with attacker email in session")

        with caplog.at_level("ERROR", logger="src.admin.app"):
            resp = client.get("/__boom_injected_email")
        assert resp.status_code == 500
        handler_records = [r for r in caplog.records if "Uncaught exception in GET" in r.getMessage()]
        assert handler_records
        msg = handler_records[0].getMessage()
        # The newline must be defanged before reaching the formatter.
        # We assert on the LITERAL escape sequence and verify NO raw \n
        # made it through. ``msg`` is the single formatted record body,
        # so even one \n would split it across two log lines.
        assert "\n" not in msg, f"raw newline leaked into log message: {msg!r}"
        assert "\\n" in msg, f"sanitized escape not present: {msg!r}"
        assert "forged log line" in msg  # the suffix survived, sanitized


class TestEmbeddedMissingPrefixWarning:
    """Embedded-auth requests without X-Forwarded-Prefix produce broken
    redirects (they bypass the proxy mount). The warning surfaces this
    misconfiguration in salesagent logs so the storefront integrator
    sees it instead of guessing why redirects land outside their iframe.
    """

    @pytest.fixture
    def app(self):
        # TESTING=False so the before_request hook runs (it bypasses
        # itself under TESTING to keep legacy unit tests quiet).
        return create_app({"TESTING": False, "SECRET_KEY": "x"})

    def test_warns_when_embedded_auth_present_without_prefix(self, app, caplog):
        client = app.test_client()
        with caplog.at_level("WARNING", logger="src.admin.app"):
            # Hit /health (a real route) so we exercise the before_request
            # hook without involving auth or DB.
            client.get(
                "/health",
                headers={
                    "X-Identity-Subject": "user@example.com",
                    "X-Identity-Email": "user@example.com",
                    "Origin": "https://interchange.io",
                },
            )
        warnings = [r for r in caplog.records if "[EMBEDDED_PREFIX_MISSING]" in r.getMessage()]
        assert warnings, f"expected EMBEDDED_PREFIX_MISSING warning; got: {[r.getMessage() for r in caplog.records]}"
        assert "/health" in warnings[0].getMessage()

    def test_no_warning_when_prefix_set(self, app, caplog):
        client = app.test_client()
        with caplog.at_level("WARNING", logger="src.admin.app"):
            client.get(
                "/health",
                headers={
                    "X-Identity-Subject": "user@example.com",
                    "X-Forwarded-Prefix": "/storefront/psa",
                },
            )
        warnings = [r for r in caplog.records if "[EMBEDDED_PREFIX_MISSING]" in r.getMessage()]
        assert not warnings

    def test_no_warning_for_non_embedded_request(self, app, caplog):
        """Plain non-embedded request (no X-Identity-Subject) must not warn."""
        client = app.test_client()
        with caplog.at_level("WARNING", logger="src.admin.app"):
            client.get("/health")
        warnings = [r for r in caplog.records if "[EMBEDDED_PREFIX_MISSING]" in r.getMessage()]
        assert not warnings

    def test_no_warning_when_x_script_name_set(self, app, caplog):
        """X-Script-Name is the alternate header the CustomProxyFix accepts."""
        client = app.test_client()
        with caplog.at_level("WARNING", logger="src.admin.app"):
            client.get(
                "/health",
                headers={
                    "X-Identity-Subject": "user@example.com",
                    "X-Script-Name": "/storefront/psa",
                },
            )
        warnings = [r for r in caplog.records if "[EMBEDDED_PREFIX_MISSING]" in r.getMessage()]
        assert not warnings
