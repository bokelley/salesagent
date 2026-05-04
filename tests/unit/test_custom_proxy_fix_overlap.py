"""Unit tests for CustomProxyFix's overlap-stripping behavior.

When an upstream proxy sets ``X-Forwarded-Prefix=/storefront/psa/tenant/<id>``
AND forwards the request with ``PATH_INFO=/tenant/<id>/<page>`` (the
storefront-iframe contract), naively using the prefix as ``SCRIPT_NAME``
causes ``url_for()`` to emit doubled paths like
``/storefront/psa/tenant/<id>/tenant/<id>/<page>``. CustomProxyFix
detects the overlap and strips the trailing ``/tenant/<id>`` segment so
absolute URLs resolve cleanly.

These tests pin the overlap-detection heuristic — guards against
regressions where future edits to CustomProxyFix accidentally re-double
the tenant or strip in cases the heuristic shouldn't fire.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def proxy_fix():
    """Build CustomProxyFix wrapping a simple WSGI app that captures the
    final environ so the test can assert on SCRIPT_NAME/PATH_INFO."""
    from src.admin.app import CustomProxyFix

    captured: dict = {}

    def inner_app(environ, start_response):
        captured["environ"] = environ
        start_response("200 OK", [])
        return [b""]

    fix = CustomProxyFix(inner_app)
    return fix, captured


def _start_response(status, headers, exc_info=None):
    pass


class TestOverlapStripping:
    def test_storefront_prefix_with_tenant_overlap_strips_tail(self, proxy_fix):
        """Prefix ends with /tenant/<id>, path starts with /tenant/<id> →
        strip the tenant tail from SCRIPT_NAME so url_for doesn't double."""
        fix, captured = proxy_fix
        environ = {
            "HTTP_X_FORWARDED_PREFIX": "/storefront/psa/tenant/tenant_abc123",
            "PATH_INFO": "/tenant/tenant_abc123/products",
        }
        list(fix(environ, _start_response))
        env = captured["environ"]
        assert env["SCRIPT_NAME"] == "/storefront/psa"
        # PATH_INFO unchanged — Flask's route lookup still hits /tenant/<id>/products
        assert env["PATH_INFO"] == "/tenant/tenant_abc123/products"

    def test_simple_admin_prefix_unchanged(self, proxy_fix):
        """Legacy /admin prefix without /tenant overlap → SCRIPT_NAME stays put,
        PATH_INFO stripped of the prefix per existing behavior."""
        fix, captured = proxy_fix
        environ = {
            "HTTP_X_FORWARDED_PREFIX": "/admin",
            "PATH_INFO": "/admin/tenant/foo/dashboard",
        }
        list(fix(environ, _start_response))
        env = captured["environ"]
        assert env["SCRIPT_NAME"] == "/admin"
        assert env["PATH_INFO"] == "/tenant/foo/dashboard"

    def test_no_overlap_when_path_is_outside_tenant(self, proxy_fix):
        """Prefix ends with /tenant/<id> but path doesn't include /tenant/<id>
        → strip nothing; let Flask 404 (caller's mismatch, not ours)."""
        fix, captured = proxy_fix
        environ = {
            "HTTP_X_FORWARDED_PREFIX": "/storefront/psa/tenant/tenant_abc",
            "PATH_INFO": "/health",
        }
        list(fix(environ, _start_response))
        env = captured["environ"]
        # SCRIPT_NAME stays as-is; PATH_INFO unchanged
        assert env["SCRIPT_NAME"] == "/storefront/psa/tenant/tenant_abc"
        assert env["PATH_INFO"] == "/health"

    def test_no_overlap_when_path_tenant_id_differs(self, proxy_fix):
        """Defensive: prefix says tenant_A, path says tenant_B → DON'T strip
        (path mismatch means the proxy did something weird; Flask 404 is the
        right outcome, not a fabricated successful match)."""
        fix, captured = proxy_fix
        environ = {
            "HTTP_X_FORWARDED_PREFIX": "/storefront/psa/tenant/tenant_A",
            "PATH_INFO": "/tenant/tenant_B/products",
        }
        list(fix(environ, _start_response))
        env = captured["environ"]
        assert env["SCRIPT_NAME"] == "/storefront/psa/tenant/tenant_A"
        assert env["PATH_INFO"] == "/tenant/tenant_B/products"

    def test_tail_with_subpaths_not_stripped(self, proxy_fix):
        """Prefix ends with /tenant/<id>/something → don't try to strip
        (tail isn't a single tenant segment)."""
        fix, captured = proxy_fix
        environ = {
            "HTTP_X_FORWARDED_PREFIX": "/storefront/psa/tenant/abc/products",
            "PATH_INFO": "/tenant/abc/products",
        }
        list(fix(environ, _start_response))
        env = captured["environ"]
        # The tail "abc/products" is multi-segment so the strip heuristic
        # doesn't fire — SCRIPT_NAME stays put.
        assert env["SCRIPT_NAME"] == "/storefront/psa/tenant/abc/products"

    def test_no_x_forwarded_prefix(self, proxy_fix):
        """No proxy header → no SCRIPT_NAME, no path mutation."""
        fix, captured = proxy_fix
        environ = {"PATH_INFO": "/tenant/abc/products"}
        list(fix(environ, _start_response))
        env = captured["environ"]
        assert "SCRIPT_NAME" not in env or env["SCRIPT_NAME"] == ""
        assert env["PATH_INFO"] == "/tenant/abc/products"
