"""Embedded-mode capability flags.

On embedded instances, the storefront (upstream host product) may absorb
workflows that the salesagent historically owned — creative approval,
Slack notifications, advertising policy, etc. ``EMBEDDED_CAPABILITIES``
is a JSON env var that names which workflows the storefront has taken
over for this salesagent instance. The salesagent hides UI and rejects
writes for any workflow owned by the storefront.

Why instance-level, not per-tenant: one embedded salesagent corresponds
to one storefront operator. The storefront decides once which workflows
it owns across all of its tenants.

Why env-var, not DB: ownership flips at the storefront's release pace,
not the salesagent's. An env-var bump is a deploy; a DB column would
need a migration plus a per-tenant rollout. Same reason ``MANAGED_INSTANCE``
is an env var.

Open instances (``MANAGED_INSTANCE`` unset or false): the env var is
ignored entirely. ``capability_owner()`` always returns ``"publisher"``,
``publisher_owns()`` always returns ``True``. There is no embedded
storefront to take ownership.

Format::

    EMBEDDED_CAPABILITIES='{"creative_approval": "storefront", "slack": "storefront"}'

Unknown keys → default ``"publisher"``. Invalid JSON or non-``str``
values → ``ValueError`` at first call (fail loud — misconfiguration
silently leaving every workflow on the publisher side would be the
worst failure mode).
"""

from __future__ import annotations

import json
import os
from typing import Literal

from src.admin.utils.embedded_mode_auth import is_managed_instance

CapabilityOwner = Literal["publisher", "storefront"]


def _parse_capabilities() -> dict[str, CapabilityOwner]:
    raw = os.environ.get("EMBEDDED_CAPABILITIES", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"EMBEDDED_CAPABILITIES is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"EMBEDDED_CAPABILITIES must be a JSON object, got {type(parsed).__name__}")
    result: dict[str, CapabilityOwner] = {}
    for key, value in parsed.items():
        if value not in ("publisher", "storefront"):
            raise ValueError(f"EMBEDDED_CAPABILITIES[{key!r}] must be 'publisher' or 'storefront', got {value!r}")
        result[key] = value
    return result


def capability_owner(name: str) -> CapabilityOwner:
    """Return ``"storefront"`` if the upstream host has taken over this
    workflow, ``"publisher"`` otherwise.

    Always returns ``"publisher"`` on open instances (no storefront to
    take ownership). Re-reads the env var on every call so a deploy
    flip takes effect without process restart.
    """
    if not is_managed_instance():
        return "publisher"
    return _parse_capabilities().get(name, "publisher")


def publisher_owns(name: str) -> bool:
    """Sugar for ``capability_owner(name) == "publisher"``. Used in
    Jinja gates: ``{% if publisher_owns('creative_approval') %}``."""
    return capability_owner(name) == "publisher"
