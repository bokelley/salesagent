"""Unit tests for ``core.main._resolve_public_url``.

The salesagent-local rewrite middleware was retired in favor of the SDK's
native ``serve(public_url=callable)`` per-request resolver (adcp 5.3.0 #680
unblocked the ``transport='both'`` composed-lifespan crash that previously
forced the middleware workaround).

These tests pin the resolver's behavior. The SDK validates the returned URL
(absolute + ``https://`` required for non-loopback) before serving the agent
card; the resolver's job is to make the right URL for this request.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from starlette.requests import Request

from core.main import _resolve_public_url


def _request(headers: dict[str, str] | None = None) -> Request:
    """Build a synthetic Starlette Request with the given header dict."""
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/.well-known/agent-card.json",
        "headers": raw_headers,
    }
    return Request(scope)


def _resolve(headers: dict[str, str] | None = None, env: dict[str, str] | None = None) -> str:
    """Drive the resolver with an isolated env so PUBLIC_URL leaks
    from CI don't influence the test outcome."""
    overrides = {"PUBLIC_URL": "", **(env or {})}
    with patch.dict(os.environ, overrides, clear=False):
        # patch.dict won't unset PUBLIC_URL when the override is empty
        # — drop it explicitly so the resolver falls through to headers.
        if not overrides.get("PUBLIC_URL"):
            os.environ.pop("PUBLIC_URL", None)
        return _resolve_public_url(_request(headers))


class TestResolverDerivesFromHeaders:
    def test_x_forwarded_host_takes_precedence_over_host(self) -> None:
        url = _resolve(
            {
                "host": "internal:8080",
                "x-forwarded-host": "wonderstruck.sales-agent.scope3.com",
                "x-forwarded-proto": "https",
            }
        )
        assert url == "https://wonderstruck.sales-agent.scope3.com/"

    def test_falls_back_to_host_when_no_xff(self) -> None:
        url = _resolve({"host": "wonderstruck.sales-agent.scope3.com"})
        assert url == "https://wonderstruck.sales-agent.scope3.com/"

    def test_strips_extra_xff_entries_from_proxy_chain(self) -> None:
        url = _resolve(
            {
                "x-forwarded-host": "wonderstruck.sales-agent.scope3.com, internal, edge",
                "x-forwarded-proto": "https",
            }
        )
        assert url == "https://wonderstruck.sales-agent.scope3.com/"

    def test_xforwarded_proto_overrides_default_https(self) -> None:
        url = _resolve(
            {
                "x-forwarded-host": "wonderstruck.sales-agent.scope3.com",
                "x-forwarded-proto": "http",
            }
        )
        # Non-loopback host with explicit http — the resolver returns the
        # caller's stated scheme even though the SDK will then reject it
        # in ``_validate_card_url``. The resolver's job is fidelity to the
        # request, not scheme policing.
        assert url == "http://wonderstruck.sales-agent.scope3.com/"

    def test_defaults_to_https_when_no_proto_header(self) -> None:
        url = _resolve({"x-forwarded-host": "wonderstruck.sales-agent.scope3.com"})
        assert url == "https://wonderstruck.sales-agent.scope3.com/"


class TestResolverLoopbackSemantics:
    """Loopback hosts must default to ``http://`` — the SDK's
    ``_validate_card_url`` accepts ``http`` only for loopback hostnames
    and rejects ``https://localhost/`` as malformed. The resolver mirrors
    that exception so the dev path doesn't fail card validation."""

    def test_localhost_defaults_to_http(self) -> None:
        url = _resolve({"host": "localhost:8080"})
        assert url == "http://localhost:8080/"

    def test_127_0_0_1_defaults_to_http(self) -> None:
        url = _resolve({"host": "127.0.0.1:8080"})
        assert url == "http://127.0.0.1:8080/"

    def test_subdomain_dot_localhost_defaults_to_http(self) -> None:
        url = _resolve({"host": "wonderstruck.localhost:8080"})
        assert url == "http://wonderstruck.localhost:8080/"


class TestResolverEnvOverride:
    def test_public_url_env_overrides_request_headers(self) -> None:
        url = _resolve(
            headers={"x-forwarded-host": "request-host.example.com"},
            env={"PUBLIC_URL": "https://configured.example.com"},
        )
        # PUBLIC_URL wins when set — single-host deploys pin the card URL.
        assert url == "https://configured.example.com/"

    def test_public_url_env_normalizes_trailing_slash(self) -> None:
        url = _resolve(env={"PUBLIC_URL": "https://configured.example.com/"})
        assert url == "https://configured.example.com/"

    def test_empty_public_url_env_falls_back_to_headers(self) -> None:
        # Empty env value must NOT short-circuit the header-derivation path.
        url = _resolve(
            headers={"x-forwarded-host": "request-host.example.com"},
            env={"PUBLIC_URL": ""},
        )
        assert url == "https://request-host.example.com/"


class TestResolverNoHeadersFallback:
    """When neither X-Forwarded-Host nor Host is present (synthetic
    requests, broken proxies), the resolver returns the SDK's localhost
    default rather than raising — keeps boot-time card construction
    working even if a request arrives with empty headers."""

    def test_no_headers_returns_localhost_default_port(self) -> None:
        url = _resolve(headers={})
        assert url == "http://localhost:3001/"

    def test_no_headers_honors_adcp_port_env(self) -> None:
        url = _resolve(headers={}, env={"ADCP_PORT": "9999"})
        assert url == "http://localhost:9999/"
