"""Unit tests for buyer-agent domain-driven resolution via brand.json.

Covers:

* Domain normalization (paste-tolerant, case-insensitive, scheme-stripping).
* SSRF guard (private/loopback/link-local/IPv4-mapped-IPv6 addresses rejected).
* brand.json walk via :class:`adcp.signing.BrandJsonJwksResolver` — happy path,
  fetch failure, agent-ambiguous error.
* Cross-domain enforcement on agent.url and jwks_uri (must be on or under
  the operator-typed domain).
* JWKS missing → ``ok=True`` (admit as bearer-only) but JWKS check fails.
* Supported-alg check flips ``signing_keys.supported_alg`` correctly.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from adcp.signing.brand_jwks import BrandJsonResolverError

from src.admin.services.buyer_agent_resolve import (
    ResolveError,
    _is_blocked_address,
    _is_same_or_subdomain,
    _normalize_domain,
    _slug_from_domain,
    _ssrf_check,
    resolve_domain,
)

# ---------------------------------------------------------------------------
# Shared test helpers (DRY: kept in one place, used across happy-path + edges).
# ---------------------------------------------------------------------------


@contextmanager
def _patch_brand_resolver(*, agent_url: str | None, jwks_uri: str | None, refresh_error: Exception | None = None):
    """Patch BrandJsonJwksResolver so .force_refresh + agent_url/jwks_uri
    properties return canned values without any real network IO.
    """
    fake = MagicMock()
    if refresh_error is not None:
        fake.force_refresh = AsyncMock(side_effect=refresh_error)
    else:
        fake.force_refresh = AsyncMock(return_value=None)
    type(fake).agent_url = PropertyMock(return_value=agent_url)
    type(fake).jwks_uri = PropertyMock(return_value=jwks_uri)
    with patch(
        "src.admin.services.buyer_agent_resolve.BrandJsonJwksResolver",
        return_value=fake,
    ):
        yield


@contextmanager
def _patch_jwks_fetch(*, keys: list[dict] | None = None, error: Exception | None = None):
    """Patch the inline JWKS fetch (used after brand.json resolves) so we can
    simulate JWKS-reachable / JWKS-missing / unsupported-alg cases without
    real HTTP.
    """
    if error is not None:
        with patch(
            "src.admin.services.buyer_agent_resolve._fetch_jwks",
            side_effect=error,
        ):
            yield
    else:
        with patch(
            "src.admin.services.buyer_agent_resolve._fetch_jwks",
            return_value=keys or [],
        ):
            yield


def _check_map(result) -> dict[str, bool]:
    return {c.name: c.ok for c in result.checks}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDomainNormalization:
    def test_strips_scheme_and_path(self):
        assert _normalize_domain("https://Interchange.IO/foo") == "interchange.io"

    def test_lowercases(self):
        assert _normalize_domain("Buyer.EXAMPLE.com") == "buyer.example.com"

    def test_rejects_invalid(self):
        with pytest.raises(ResolveError):
            _normalize_domain("not a domain")

    def test_rejects_empty(self):
        with pytest.raises(ResolveError):
            _normalize_domain("")


class TestSlug:
    def test_dots_become_underscores(self):
        assert _slug_from_domain("interchange.io") == "interchange_io"

    def test_dashes_become_underscores(self):
        assert _slug_from_domain("buyer-corp.example.com") == "buyer_corp_example_com"


class TestSameOrSubdomain:
    @pytest.mark.parametrize(
        "host,domain,expected",
        [
            ("interchange.io", "interchange.io", True),
            ("agents.interchange.io", "interchange.io", True),
            ("a.b.interchange.io", "interchange.io", True),
            ("interchange.io.attacker.com", "interchange.io", False),  # suffix-bypass attempt
            ("victim.com", "interchange.io", False),
            ("", "interchange.io", False),
        ],
    )
    def test_strict_subdomain(self, host, domain, expected):
        assert _is_same_or_subdomain(host, domain) is expected


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "addr",
        [
            "127.0.0.1",  # loopback
            "10.0.0.1",  # RFC1918
            "192.168.1.1",  # RFC1918
            "172.16.0.1",  # RFC1918
            "169.254.169.254",  # link-local (cloud metadata!)
            "::1",  # IPv6 loopback
            "fd00::1",  # IPv6 unique local
            "224.0.0.1",  # multicast
            "::ffff:127.0.0.1",  # IPv4-mapped IPv6 — must unwrap and re-check
            "::ffff:10.0.0.1",
        ],
    )
    def test_blocks_unsafe_addresses(self, addr):
        assert _is_blocked_address(addr) is True

    @pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
    def test_allows_public_addresses(self, addr):
        assert _is_blocked_address(addr) is False

    def test_ssrf_check_raises_for_private_host(self):
        with patch("src.admin.services.buyer_agent_resolve._resolve_addresses", return_value=["10.0.0.1"]):
            with pytest.raises(ResolveError, match="non-public"):
                _ssrf_check("internal.example.com")


class TestResolveDomainHappyPath:
    def test_full_preview_with_supported_jwks(self):
        with (
            _patch_brand_resolver(
                agent_url="https://interchange.io/agent",
                jwks_uri="https://interchange.io/keys",
            ),
            _patch_jwks_fetch(keys=[{"kid": "k1", "alg": "EdDSA"}]),
        ):
            result = resolve_domain("interchange.io")

        assert result.ok is True
        assert result.agent_url == "https://interchange.io/agent"
        assert result.jwks_uri == "https://interchange.io/keys"
        assert result.principal_id == "interchange_io"
        # Display name falls back to domain (brand.json has no name field).
        assert result.name == "interchange.io"
        assert result.signing_keys == [{"kid": "k1", "alg": "EdDSA"}]
        checks = _check_map(result)
        assert checks["brand_json.fetched"] is True
        assert checks["agent_url.same_domain"] is True
        assert checks["jwks_uri.same_domain"] is True
        assert checks["jwks.fetched"] is True
        assert checks["jwks.supported_alg"] is True

    def test_subdomain_agent_url_accepted(self):
        with (
            _patch_brand_resolver(
                agent_url="https://agents.interchange.io/buyer",
                jwks_uri="https://agents.interchange.io/buyer/.well-known/jwks.json",
            ),
            _patch_jwks_fetch(keys=[{"kid": "k1", "alg": "ES256"}]),
        ):
            result = resolve_domain("interchange.io")
        assert result.ok is True
        assert _check_map(result)["agent_url.same_domain"] is True


class TestResolveDomainEdgeCases:
    def test_brand_json_fetch_failure(self):
        with _patch_brand_resolver(
            agent_url=None,
            jwks_uri=None,
            refresh_error=BrandJsonResolverError("invalid_url", "bad URL"),
        ):
            result = resolve_domain("interchange.io")
        assert result.ok is False
        assert "could not resolve brand.json" in (result.error or "")
        assert _check_map(result)["brand_json.fetched"] is False

    def test_cross_domain_agent_url_rejected(self):
        # H1 from security review — buyer's brand.json declares an off-domain
        # agent.url to turn admit into a cross-domain SSRF probe. Must reject.
        with _patch_brand_resolver(
            agent_url="https://victim.com/agent",
            jwks_uri="https://victim.com/jwks",
        ):
            result = resolve_domain("interchange.io")
        assert result.ok is False
        assert "is not on or under" in (result.error or "")
        assert _check_map(result)["agent_url.same_domain"] is False

    def test_cross_domain_jwks_uri_rejected(self):
        # Variant of H1: agent_url is on-domain but jwks_uri is off-domain.
        with _patch_brand_resolver(
            agent_url="https://interchange.io/agent",
            jwks_uri="https://victim.com/keys",
        ):
            result = resolve_domain("interchange.io")
        assert result.ok is False
        assert _check_map(result)["jwks_uri.same_domain"] is False

    def test_jwks_missing_still_returns_ok_for_bearer_admit(self):
        # Discovery worked → preview is valid; JWKS just isn't there yet
        # (operator can admit as bearer-only).
        with (
            _patch_brand_resolver(
                agent_url="https://buyer.example.com",
                jwks_uri="https://buyer.example.com/keys",
            ),
            _patch_jwks_fetch(error=ResolveError("HTTP 404")),
        ):
            result = resolve_domain("buyer.example.com")
        assert result.ok is True
        assert result.agent_url == "https://buyer.example.com"
        assert result.signing_keys == []
        assert _check_map(result)["jwks.fetched"] is False

    def test_unsupported_alg_flagged(self):
        with (
            _patch_brand_resolver(
                agent_url="https://buyer.example.com",
                jwks_uri="https://buyer.example.com/keys",
            ),
            _patch_jwks_fetch(keys=[{"kid": "k1", "alg": "RS512"}]),
        ):  # RS512 not supported
            result = resolve_domain("buyer.example.com")
        checks = _check_map(result)
        assert checks["jwks.has_keys"] is True
        assert checks["jwks.supported_alg"] is False

    def test_invalid_domain_short_circuits_before_fetch(self):
        with patch("src.admin.services.buyer_agent_resolve.BrandJsonJwksResolver") as mock_cls:
            result = resolve_domain("not a domain at all")
        assert result.ok is False
        assert "not a valid domain" in (result.error or "")
        mock_cls.assert_not_called()
