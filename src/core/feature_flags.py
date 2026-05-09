"""Tenant-level feature flag accessors.

Each public function takes a ``Tenant`` ORM instance (or a dict produced
by ``get_tenant_by_id``) and returns the resolved boolean for the flag.
Functions accept ``None`` and return the default — the caller doesn't
have to short-circuit on missing context.

Pattern: every flag has a single accessor here. Adding new flags adds
a new column on ``Tenant`` plus one function below. No env-var
overrides at this layer — that's a separate concern handled by
the ``ENVIRONMENT`` config when it applies. All flag state is
durable, per-tenant, and visible to operators via the admin UI.
"""

from __future__ import annotations

from typing import Any


def _flag_value(tenant: Any | None, attr: str, default: bool = False) -> bool:
    """Resolve a flag from a Tenant ORM instance or tenant dict.

    Operators sometimes pass a dict (from ``get_tenant_by_id``) and
    sometimes pass the ORM instance. Treat both uniformly. Falsy
    inputs return the default.
    """
    if tenant is None:
        return default
    if isinstance(tenant, dict):
        return bool(tenant.get(attr, default))
    return bool(getattr(tenant, attr, default))


def is_creative_pre_approval_gate_enabled(tenant: Any | None) -> bool:
    """Return True when the tenant has the creative pre-approval gate on.

    When True, creatives at ``status='pending_review'`` are held back
    from the ad-server upload during ``create_media_buy`` /
    ``execute_approved_media_buy``. They reach the ad server only when
    a human (or the AI auto-review path) flips the local status to
    ``approved``, which triggers a retroactive upload + LICA against
    the already-live line item.

    Default ``False`` preserves today's execute-then-gate behaviour
    byte-for-byte. Flag is per-tenant on the ``tenants`` table:
    ``tenants.creative_pre_approval_gate_enabled`` (#145).
    """
    return _flag_value(tenant, "creative_pre_approval_gate_enabled", default=False)
