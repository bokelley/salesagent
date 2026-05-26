"""adagents.json fetch helpers used by publisher setup flows."""

from __future__ import annotations

import json
from typing import Any

import httpx
from adcp import AdagentsValidationError, fetch_adagents

_MAX_ADAGENTS_BYTES = 5 * 1024 * 1024
_BARE_AUTHORIZATION_ERROR = "Agent authorization must have exactly one of"


async def fetch_adagents_permissive(
    publisher_domain: str,
    *,
    timeout: float = 10.0,
    user_agent: str = "AdCP-Client/1.0",
) -> dict[str, Any]:
    """Fetch adagents.json, allowing bare authorized-agent entries.

    The AdCP SDK's strict fetch rejects files whose ``authorized_agents``
    entries omit ``authorization_type``. The salesagent intentionally treats
    that specific shape as operationally usable when a top-level
    ``properties[]`` block exists, so the domain-first bundle setup needs the
    parsed JSON rather than a hard fetch failure.

    For every other failure mode, the SDK exception is preserved.
    """
    try:
        return await fetch_adagents(publisher_domain, timeout=timeout, user_agent=user_agent)
    except AdagentsValidationError as exc:
        if _BARE_AUTHORIZATION_ERROR not in str(exc):
            raise
        return await _fetch_direct_json(publisher_domain, timeout=timeout, user_agent=user_agent)


async def _fetch_direct_json(publisher_domain: str, *, timeout: float, user_agent: str) -> dict[str, Any]:
    url = f"https://{publisher_domain}/.well-known/adagents.json"
    headers = {"User-Agent": user_agent}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    body = response.content
    if len(body) > _MAX_ADAGENTS_BYTES:
        raise AdagentsValidationError("adagents.json exceeds maximum size")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AdagentsValidationError(f"Invalid JSON in adagents.json: {exc}") from exc
    if not isinstance(data, dict):
        raise AdagentsValidationError("adagents.json must be a JSON object")
    if "authorized_agents" not in data and "authoritative_location" not in data:
        raise AdagentsValidationError("adagents.json must have either 'authorized_agents' or 'authoritative_location'")
    if "authorized_agents" in data and not isinstance(data["authorized_agents"], list):
        raise AdagentsValidationError("'authorized_agents' must be an array")
    return data
