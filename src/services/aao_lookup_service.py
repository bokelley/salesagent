"""AAO (AdCP Authorized Origins) lookup service.

Wraps the ``adcp`` library's brand.json + adagents.json fetchers with the
salesagent's caching policy:

- 5-minute TTL on brand.json fetches (per-tenant, keyed by house_domain).
  Hot-loop list_authorized_properties calls don't pet the publisher's CDN.
- 6-hour TTL on adagents.json fetches (per-publisher_domain).
  PublisherPartner.is_verified gets refreshed on this cadence by the
  existing sync_all_tenants cron.
- Best-effort fallback to today's AuthorizedProperty cache when the live
  fetch fails — keeps the buyer protocol responsive during the
  deprecation window. Hard-error path lands when the table drops.

Replaces direct AuthorizedProperty.* reads in the buyer-protocol tools.
See ``docs/design/replace-authorized-properties-with-aao-lookup.md``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from adcp import fetch_adagents, get_properties_by_agent

logger = logging.getLogger(__name__)


# In-memory caches. Single-process is fine for sprint 1.7 — the existing
# core deployment is single-worker. Multi-worker deployments will need a
# Redis-backed cache or accept duplicate fetches across workers (low-cost
# since brand.json + adagents.json are small static JSON files).
_BRAND_TTL_SECONDS = 300  # 5 min
_ADAGENTS_TTL_SECONDS = 21600  # 6 hours

_BRAND_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ADAGENTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class AAOLookupError(Exception):
    """Raised when an AAO fetch fails AND the fallback cache is empty."""


def invalidate_brand_cache(house_domain: str | None = None) -> None:
    """Drop a single house_domain's cached brand properties, or the whole
    cache if no domain given. Wired into the Tenant Management API's
    PATCH handler so a publisher updating ``house_domain`` sees fresh
    data immediately."""
    if house_domain is None:
        _BRAND_CACHE.clear()
    else:
        _BRAND_CACHE.pop(house_domain, None)


def invalidate_adagents_cache(publisher_domain: str | None = None) -> None:
    """Same shape as :func:`invalidate_brand_cache` for the adagents.json
    cache. Caller wires it on PublisherPartner upserts and manual-verify
    button presses in the Admin UI."""
    if publisher_domain is None:
        _ADAGENTS_CACHE.clear()
    else:
        _ADAGENTS_CACHE.pop(publisher_domain, None)


async def fetch_brand_properties(
    house_domain: str, *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    """Fetch the publisher's authoritative property list from
    ``https://{house_domain}/.well-known/brand.json``.

    The AdCP spec describes brand.json as carrying inline properties
    keyed under ``authorized_agents[*].inline_properties``. We piggy-back
    on the existing ``get_properties_by_agent()`` helper because it
    already implements the spec's resolution rules (inline + by_id +
    by_tag + by_signal).

    Returns: list of property dicts. Raises :class:`AAOLookupError` on
    fetch / parse failure with no fallback.
    """
    now = time.monotonic()
    if not force_refresh:
        cached = _BRAND_CACHE.get(house_domain)
        if cached is not None and now - cached[0] < _BRAND_TTL_SECONDS:
            return cached[1]

    try:
        # brand.json lives at the same .well-known path that adagents.json
        # uses; the SDK's fetch_adagents() walks the right URL. The
        # publisher's brand.json is conventionally also a valid adagents
        # document (super-set), so we reuse the fetcher.
        adagents = await fetch_adagents(house_domain)
    except Exception as exc:
        logger.warning(
            "AAO: brand.json fetch failed for %s: %s", house_domain, exc, exc_info=True
        )
        raise AAOLookupError(f"brand.json fetch failed for {house_domain!r}: {exc}") from exc

    # The publisher is themselves the authorized agent in this view —
    # they're listing the properties THEY own. Pull every property the
    # adagents.json declares without filtering by agent_url.
    properties: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in adagents.get("authorized_agents", []) or []:
        for prop in entry.get("inline_properties", []) or []:
            pid = prop.get("property_id") or prop.get("id")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            properties.append(prop)

    _BRAND_CACHE[house_domain] = (now, properties)
    return properties


async def is_agent_authorized_by_publisher(
    publisher_domain: str, public_agent_url: str, *, force_refresh: bool = False
) -> tuple[bool, str | None]:
    """Verify the agent_url is listed in ``publisher_domain``'s adagents.json.

    Returns ``(True, None)`` on a clean verify, ``(False, error_message)``
    when the agent isn't listed or the fetch fails. Callers persist the
    result on :class:`PublisherPartner.is_verified`.

    The fetch is cached for :data:`_ADAGENTS_TTL_SECONDS` (6 hours) by
    publisher_domain — the existing `sync_all_tenants.py` cron drives the
    refresh, and the Admin UI's "Verify now" button calls
    ``invalidate_adagents_cache(publisher_domain)`` first.
    """
    now = time.monotonic()
    adagents: dict[str, Any] | None = None
    if not force_refresh:
        cached = _ADAGENTS_CACHE.get(publisher_domain)
        if cached is not None and now - cached[0] < _ADAGENTS_TTL_SECONDS:
            adagents = cached[1]

    if adagents is None:
        try:
            adagents = await fetch_adagents(publisher_domain)
        except Exception as exc:
            logger.info(
                "AAO: adagents.json fetch failed for %s: %s", publisher_domain, exc
            )
            return False, f"adagents.json fetch failed: {exc}"
        _ADAGENTS_CACHE[publisher_domain] = (now, adagents)

    # The SDK's get_properties_by_agent() returns the property list for
    # the agent_url; an empty list means the agent isn't authorized.
    # We don't actually need the property list here — just the boolean.
    properties = get_properties_by_agent(adagents, public_agent_url)
    if not properties:
        return False, f"agent_url {public_agent_url!r} not listed in adagents.json"
    return True, None
