"""Domain-driven buyer-agent discovery via brand.json.

Operator types a buyer's domain (e.g. ``interchange.io``); we fetch the
buyer's published ``brand.json`` discovery doc through
:class:`adcp.signing.BrandJsonJwksResolver` (which gives us SSRF protection,
DNS-rebinding-safe IP-pinned transport, redirect+body caps, schema
validation, and same-origin guard on the implicit ``jwks_uri`` fallback —
all delegated to the library so we don't roll our own).

After resolution we layer two extra checks on top of the library's defaults:

1. **Cross-domain enforcement on ``agent.url`` + ``agent.jwks_uri``**: the
   library doesn't enforce same-domain on an *explicit* ``jwks_uri``. A
   buyer publishing brand.json on ``buyer.example.com`` and declaring
   ``jwks_uri=https://victim.com/jwks.json`` would otherwise turn admit-time
   resolution into a cross-domain SSRF probe. We require the agent_url and
   jwks_uri host to equal or be a subdomain of the operator-typed domain.

2. **Static signing readiness**: fetch ``jwks_uri``, enumerate keys, flag
   whether at least one declares a supported ``alg``. Surfaces "buyer hasn't
   set up signing yet" before the operator clicks save.

The preview returned to the admin UI never raises — every failure path
populates :class:`ResolveResult` with ``ok=False`` so the form can offer
manual fallback.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from adcp.signing.brand_jwks import (
    BrandJsonJwksResolver,
    BrandJsonResolverError,
)

logger = logging.getLogger(__name__)

# Algs we'll actually verify against. Mirrors what adcp.signing supports.
SUPPORTED_ALGS: frozenset[str] = frozenset({"EdDSA", "ES256", "RS256"})

# JWKS fetch hard caps. brand.json itself goes through the library's
# (stricter) caps; these apply only to our own JWKS fetch for the preview.
JWKS_FETCH_TIMEOUT_SECONDS = 5.0
JWKS_MAX_BYTES = 64 * 1024
JWKS_MAX_REDIRECTS = 2

# RFC 1123 hostname (lowercased input). No scheme, no path.
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ResolveResult:
    """Preview returned to the admin UI."""

    ok: bool
    domain: str
    name: str | None = None
    agent_url: str | None = None
    jwks_uri: str | None = None
    principal_id: str | None = None
    signing_keys: list[dict[str, str]] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "domain": self.domain,
            "name": self.name,
            "agent_url": self.agent_url,
            "jwks_uri": self.jwks_uri,
            "principal_id": self.principal_id,
            "signing_keys": self.signing_keys,
            "checks": [asdict(c) for c in self.checks],
            "error": self.error,
        }


class ResolveError(Exception):
    """Anything that prevents producing a preview."""


# ---------------------------------------------------------------------------
# Helpers used by both the brand.json walk and the JWKS preview-fetch.
# ---------------------------------------------------------------------------


def _normalize_domain(raw: str) -> str:
    """Strip scheme/path/whitespace; lowercase. Validate as a bare hostname."""
    s = (raw or "").strip().lower()
    if "://" in s:
        s = urlsplit(s).hostname or ""
    s = s.rstrip("/").split("/", 1)[0]
    if not _DOMAIN_RE.match(s):
        raise ResolveError(f"{raw!r} is not a valid domain")
    return s


def _slug_from_domain(domain: str) -> str:
    """Turn ``interchange.io`` into ``interchange_io`` (a valid principal_id)."""
    return re.sub(r"[^a-z0-9]+", "_", domain).strip("_")


def _is_same_or_subdomain(host: str, domain: str) -> bool:
    """True if ``host`` equals ``domain`` or is a subdomain of it.

    Strict equality + suffix-with-dot prevents the ``buyer.com.attacker.com``
    bypass that a naive ``endswith(domain)`` admits.
    """
    h = (host or "").lower()
    d = domain.lower()
    return h == d or h.endswith("." + d)


def _resolve_addresses(host: str) -> list[str]:
    """Return all IPv4/IPv6 addresses for ``host`` (DNS lookup)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ResolveError(f"DNS lookup failed for {host!r}: {exc}") from exc
    # info[4] is the sockaddr tuple; info[4][0] is the host string
    # (IPv4: (host, port); IPv6: (host, port, flowinfo, scopeid)).
    # Coerce to str so mypy accepts the deduplicating set.
    return list({str(info[4][0]) for info in infos})


def _is_blocked_address(addr: str) -> bool:
    """True if ``addr`` is loopback, link-local, private, multicast, or reserved.

    Handles IPv4-mapped IPv6 (``::ffff:127.0.0.1``) by extracting the IPv4
    form and re-checking — Python's ``ipaddress`` flags such addresses as
    public-IPv6 by default, missing the loopback they actually represent.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    # Unwrap IPv4-mapped IPv6 so e.g. ::ffff:127.0.0.1 doesn't slip through.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def _ssrf_check(host: str) -> str:
    """Resolve ``host`` and return the first public IP. Raise on any non-public IP.

    Returns the resolved IP so the caller can pin the connection to it (DNS
    pinning — prevents the rebind window between this check and the actual
    GET).
    """
    addrs = _resolve_addresses(host)
    if not addrs:
        raise ResolveError(f"{host!r} has no addresses")
    for addr in addrs:
        if _is_blocked_address(addr):
            raise ResolveError(f"refusing to fetch from {host!r}: resolves to non-public address")
    return addrs[0]


def _fetch_jwks(jwks_uri: str, *, domain: str) -> list[dict[str, str]]:
    """Fetch + parse a JWKS document, enforcing SSRF / size / redirect caps
    AND cross-domain on every redirect hop.

    Uses :func:`adcp.signing.ip_pinned_transport.build_ip_pinned_transport`
    so the IP we SSRF-checked is the IP we connect to (closes the
    DNS-rebind window between resolve-time check and connect-time resolve).

    ``domain`` is the operator-typed buyer domain. Every redirect target
    must be on or under it — without this, a redirect from
    ``jwks.buyer.com`` → ``victim.com/jwks.json`` would slip past the
    initial cross-domain enforcement at the call site.

    Returns ``[{kid, alg}, ...]`` summary; raises :class:`ResolveError` on
    anything that prevents a clean parse. Streams the body so the size cap
    aborts the transfer before buffering exceeds the limit.
    """
    from adcp.signing.ip_pinned_transport import build_ip_pinned_transport

    seen: set[str] = set()
    current = jwks_uri
    for _ in range(JWKS_MAX_REDIRECTS + 1):
        if current in seen:
            raise ResolveError(f"jwks redirect loop at {current!r}")
        seen.add(current)
        parts = urlsplit(current)
        if parts.scheme != "https":
            raise ResolveError(f"jwks_uri must be https:// (got {parts.scheme!r})")
        if not parts.hostname:
            raise ResolveError("jwks_uri has no hostname")
        if not _is_same_or_subdomain(parts.hostname, domain):
            raise ResolveError(f"jwks redirect target {parts.hostname!r} is not on or under {domain!r}")
        # SSRF-checked + IP-pinned: build_ip_pinned_transport resolves once,
        # rejects private/loopback, and pins the connection so DNS rebinding
        # between SSRF check and connect can't redirect us into RFC1918.
        transport = build_ip_pinned_transport(current, allow_private=False)

        with httpx.Client(transport=transport, timeout=JWKS_FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise ResolveError(f"jwks redirect from {current!r} with no Location header")
                    current = str(httpx.URL(current).join(loc))
                    continue
                if resp.status_code != 200:
                    raise ResolveError(f"GET {current!r} returned HTTP {resp.status_code}")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > JWKS_MAX_BYTES:
                        raise ResolveError(f"jwks response from {current!r} exceeds {JWKS_MAX_BYTES} bytes")
        try:
            import json as _json

            doc = _json.loads(bytes(buf))
        except ValueError as exc:
            raise ResolveError(f"jwks response from {current!r} is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise ResolveError(f"jwks response from {current!r} is not a JSON object")
        keys = doc.get("keys")
        if not isinstance(keys, list):
            return []
        return [
            {"kid": str(k.get("kid", "")) or "<no-kid>", "alg": str(k.get("alg", "")) or "<no-alg>"}
            for k in keys
            if isinstance(k, dict)
        ]
    raise ResolveError(f"too many redirects fetching {jwks_uri!r}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def resolve_domain(raw_domain: str) -> ResolveResult:
    """Resolve a buyer's domain to an admit-time preview.

    Walks ``https://<domain>/.well-known/brand.json`` via
    :class:`BrandJsonJwksResolver` (library handles SSRF/DNS-pinning/body cap/
    redirect cap/schema validation/same-origin guard for fallback JWKS),
    enforces cross-domain on the resolved ``agent_url`` and ``jwks_uri``,
    then fetches the JWKS to enumerate keys + flag supported-alg readiness.

    Never raises — all failures populate ``ResolveResult.error`` so the UI
    can render a manual-entry fallback.
    """
    try:
        domain = _normalize_domain(raw_domain)
    except ResolveError as exc:
        return ResolveResult(ok=False, domain=raw_domain, error=str(exc))

    result = ResolveResult(ok=False, domain=domain, principal_id=_slug_from_domain(domain))
    brand_json_url = f"https://{domain}/.well-known/brand.json"

    resolver = BrandJsonJwksResolver(
        brand_json_url=brand_json_url,
        agent_type="buying",
    )
    try:
        asyncio.run(resolver.force_refresh())
    except BrandJsonResolverError as exc:
        result.error = f"could not resolve brand.json: {exc}"
        result.checks.append(CheckResult("brand_json.fetched", False, str(exc)))
        return result
    except Exception as exc:
        result.error = f"brand.json fetch crashed: {exc}"
        result.checks.append(CheckResult("brand_json.fetched", False, str(exc)))
        return result
    result.checks.append(CheckResult("brand_json.fetched", True, brand_json_url))

    agent_url = resolver.agent_url
    jwks_uri = resolver.jwks_uri
    if not agent_url or not jwks_uri:
        result.error = "brand.json resolution produced no agent_url / jwks_uri"
        return result

    # Cross-domain enforcement: agent_url + jwks_uri must be on or under the
    # operator-typed domain. Stops a malicious brand.json from declaring
    # `agent.url=https://victim.com` and turning admit into an SSRF probe.
    agent_host = (urlsplit(agent_url).hostname or "").lower()
    jwks_host = (urlsplit(jwks_uri).hostname or "").lower()
    if not _is_same_or_subdomain(agent_host, domain):
        result.error = f"agent.url host {agent_host!r} is not on or under {domain!r}"
        result.checks.append(CheckResult("agent_url.same_domain", False, result.error))
        return result
    result.checks.append(CheckResult("agent_url.same_domain", True, agent_host))

    if not _is_same_or_subdomain(jwks_host, domain):
        result.error = f"jwks_uri host {jwks_host!r} is not on or under {domain!r}"
        result.checks.append(CheckResult("jwks_uri.same_domain", False, result.error))
        return result
    result.checks.append(CheckResult("jwks_uri.same_domain", True, jwks_host))

    result.agent_url = agent_url.rstrip("/")
    result.jwks_uri = jwks_uri
    # No display name field in brand.json schema — fall back to the domain
    # (operator can edit the name field before saving).
    result.name = domain

    # JWKS reachability + alg readiness check (IP-pinned + per-hop
    # cross-domain enforcement on redirects, see _fetch_jwks docstring).
    try:
        keys = _fetch_jwks(jwks_uri, domain=domain)
    except ResolveError as exc:
        result.checks.append(CheckResult("jwks.fetched", False, str(exc)))
        # brand.json was valid, JWKS just isn't ready — operator can still
        # admit as bearer-only. Preview is OK.
        result.ok = True
        return result
    result.checks.append(CheckResult("jwks.fetched", True, jwks_uri))

    result.signing_keys = keys
    if not keys:
        result.checks.append(CheckResult("jwks.has_keys", False, "JWKS contains no keys"))
    else:
        result.checks.append(CheckResult("jwks.has_keys", True, f"{len(keys)} key(s) declared"))
        supported = [k for k in keys if k["alg"] in SUPPORTED_ALGS]
        result.checks.append(
            CheckResult(
                "jwks.supported_alg",
                len(supported) > 0,
                (
                    f"{len(supported)} key(s) use a supported alg ({', '.join(sorted(SUPPORTED_ALGS))})"
                    if supported
                    else "no keys declare a supported alg"
                ),
            )
        )

    result.ok = True
    return result
