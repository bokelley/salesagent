"""Shape helpers for ``authorized_agents[]`` entries shared between
:mod:`src.services.aao_lookup_service` (chip-state classifier) and
:mod:`src.services.property_discovery_service` (property syncer).

Both services need the same "is this entry bare?" / "where is our agent?"
predicates. Keeping them here avoids duplicate definitions drifting apart
when the AdCP spec adds a new selector field (see salesagent#377 and
adcp#4478 for the unbound-state context).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from adcp import get_properties_by_agent as sdk_get_properties_by_agent
from adcp.adagents import normalize_url

# Every selector field the AdCP schema's authorized_agents oneOf
# discriminator pairs with an ``authorization_type``. Source of truth:
# https://adcontextprotocol.org/schemas/v1/adagents.json — the six oneOf
# variants. If the spec adds a new variant (e.g. adcp#4478's
# ``all_top_level_properties``), update this tuple so the bare-entry
# detector keeps matching the schema. Order is not meaningful.
_KNOWN_SELECTOR_FIELDS: tuple[str, ...] = (
    "property_ids",
    "property_tags",
    "properties",
    "publisher_properties",
    "signal_ids",
    "signal_tags",
)


def is_bare_entry(entry: dict[str, Any]) -> bool:
    """True when an ``authorized_agents`` entry carries no
    ``authorization_type`` AND none of the schema's selector fields.

    Bare entries don't match any ``oneOf`` branch and are therefore
    schema-invalid, but real publishers (wonderstruck.org, Raptive) ship
    them. The chip + property-sync layers interpret them permissively as
    "authorized for all top-level properties" — see the ``unbound`` state
    in :mod:`src.services.aao_lookup_service`.
    """
    if entry.get("authorization_type"):
        return False
    return not any(entry.get(field) for field in _KNOWN_SELECTOR_FIELDS)


def find_agent_entry(adagents: dict[str, Any], agent_url: str) -> dict[str, Any] | None:
    """Return the ``authorized_agents`` entry whose ``url`` matches
    ``agent_url`` under the SDK's protocol-insensitive normalization, or
    None if the agent isn't listed.

    Drives the unbound/pending fork: "we're listed but not bound" and
    "we're not listed at all" need different remediation, but the SDK's
    ``get_properties_by_agent`` collapses both into an empty list.
    """
    target = normalize_url(agent_url)
    for entry in adagents.get("authorized_agents", []) or []:
        if not isinstance(entry, dict):
            continue
        if normalize_url(entry.get("url", "")) == target:
            return entry
    return None


def top_level_properties(adagents: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the file's top-level ``properties[]`` array as dicts only.

    The permissive ``unbound`` resolution binds to this array when our
    agent's entry is bare. Filtering out non-dict entries keeps the
    permissive path defensive against malformed input — the SDK's strict
    path enforces the same invariant on typed bindings.
    """
    props = adagents.get("properties")
    if not isinstance(props, list):
        return []
    return [p for p in props if isinstance(p, dict)]


def _has_string_values(value: Any) -> bool:
    """True when a selector list contains at least one non-empty string."""
    return isinstance(value, list) and any(isinstance(item, str) and item for item in value)


def _normalize_compact_selector(selector: dict[str, Any]) -> dict[str, Any] | None:
    """Convert dict-form publisher_properties into an SDK selector list item.

    This is intentionally only a container-shape adapter. The SDK remains
    responsible for exact domain matching, revocations, selection rules, and
    deduplication. We fail closed when the dict mixes singular and compact
    domain fields because that is not a safe, unambiguous shape to normalize.
    """
    has_singular = "publisher_domain" in selector
    has_compact = "publisher_domains" in selector
    if has_singular == has_compact:
        return None

    normalized = dict(selector)
    if "selection_type" not in normalized:
        if _has_string_values(normalized.get("property_ids")):
            normalized["selection_type"] = "by_id"
        elif _has_string_values(normalized.get("property_tags")):
            normalized["selection_type"] = "by_tag"
        else:
            normalized["selection_type"] = "all"
    return normalized


def _adagents_with_normalized_publisher_properties(
    adagents: dict[str, Any], agent_url: str, selector: dict[str, Any]
) -> dict[str, Any] | None:
    """Return an adagents copy with the matching agent's dict selector listified."""
    normalized_selector = _normalize_compact_selector(selector)
    if normalized_selector is None:
        return None

    patched = deepcopy(adagents)
    authorized_agents = patched.get("authorized_agents")
    if not isinstance(authorized_agents, list):
        return None

    target = normalize_url(agent_url)
    for agent in authorized_agents:
        if not isinstance(agent, dict):
            continue
        if normalize_url(agent.get("url", "")) == target:
            agent["publisher_properties"] = [normalized_selector]
            return patched
    return None


def get_authorized_properties_by_agent(adagents: dict[str, Any], agent_url: str) -> list[dict[str, Any]]:
    """Resolve properties authorized for ``agent_url`` with local compat fixes.

    Delegates to the AdCP SDK first. If it returns no properties for a
    ``publisher_properties`` entry using the compact dict shape, normalize
    only that container shape into the SDK's list form and delegate again.
    """
    properties = sdk_get_properties_by_agent(adagents, agent_url)
    if properties:
        return properties

    entry = find_agent_entry(adagents, agent_url)
    if not isinstance(entry, dict) or entry.get("authorization_type") != "publisher_properties":
        return properties

    publisher_properties = entry.get("publisher_properties")
    if not isinstance(publisher_properties, dict):
        return properties

    patched = _adagents_with_normalized_publisher_properties(adagents, agent_url, publisher_properties)
    if patched is None:
        return properties
    return sdk_get_properties_by_agent(patched, agent_url)
