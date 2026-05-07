"""Model-layer write guard for platform-managed tenant surfaces.

Sprint 1 of [embedded-mode](../../../docs/design/embedded-mode.md):
the boundary between platform-managed and publisher-managed surfaces is
infrastructure vs. business. Platform-managed surfaces (Tenant core columns,
AdapterConfig) are locked to the Tenant Management API on tenants flagged
`is_embedded=True`. Publisher-managed surfaces (Product, Principal,
Creative, Workflow, etc.) remain writable from the UI for embedded tenants.

Enforcement: SQLAlchemy mapper-level `before_insert`/`before_update` listeners
inspect the active session for an authorization flag set by the management
API entrypoint (or a super-admin override for ops). Any write to a
platform-managed surface from any other code path raises
:class:`EmbeddedTenantWriteError`.

API endpoints set the flag on entry::

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        ...

Super-admin tooling sets ``session.info["super_admin_override"] = True`` for
emergency manual mutations.

Importing this module attaches the listeners as a side effect; no further
wiring is required.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from src.core.database.models import (
    AdapterConfig,
    Tenant,
    TenantSigningCredential,
    TenantSigningPolicy,
)


class EmbeddedTenantWriteError(Exception):
    """Raised when a non-API caller mutates a platform-managed surface on an embedded tenant."""


# Per-table allow-list of fields a non-API caller may still write on an embedded tenant.
# Anything not in this set (or any platform-managed table not listed at all) is locked.
# Today's Tenant model has no publisher-writable platform-managed columns — name,
# billing_plan, is_active, external_* are all platform concerns. Listed empty for clarity.
PUBLISHER_WRITABLE_FIELDS: dict[type, set[str]] = {
    Tenant: set(),
    # Sprint 1.8: gam_sandbox_advertiser_id is a runtime cache populated lazily
    # by the routing chain on first sandbox call (not a user-editable surface).
    # Routing-chain writes are internal infrastructure, not publisher UI traffic.
    AdapterConfig: {"gam_sandbox_advertiser_id"},
}


def _caller_is_authorized(target: Any, connection: Any) -> bool:
    """Return True if the active session/connection is allowed to mutate platform-managed state.

    Accepts the flag from either the session ``info`` dict (typical: API endpoints set it
    on the session they're using) or the connection ``info`` dict (fallback for code paths
    that operate at the Core SQL level without a Session). Either is sufficient.
    """
    # Prefer the session attached to the target — that's what API endpoints actually mutate.
    session = Session.object_session(target)
    session_info = getattr(session, "info", None)
    if session_info and (session_info.get("management_api_caller") or session_info.get("super_admin_override")):
        return True

    connection_info = getattr(connection, "info", None)
    if connection_info and (
        connection_info.get("management_api_caller") or connection_info.get("super_admin_override")
    ):
        return True

    return False


def _changed_fields(mapper, target) -> set[str]:
    """Return the set of *column* attribute names that have unsaved changes on ``target``.

    Only column properties are inspected. Relationship properties are excluded — adding
    a child row (Product, Principal, etc.) shows up in the parent Tenant's relationship
    history, but represents a publisher-managed write, not a platform-managed mutation.
    """
    changed = set()
    for col in mapper.column_attrs:
        history = get_history(target, col.key)
        if history.has_changes():
            changed.add(col.key)
    return changed


def _resolve_embedded_flag(target: Any, connection: Any) -> bool:
    """Return True if the parent tenant of ``target`` is flagged ``is_embedded``."""
    if isinstance(target, Tenant):
        return bool(target.is_embedded)

    tenant_id = getattr(target, "tenant_id", None)
    if not tenant_id:
        return False

    result = connection.execute(select(Tenant.is_embedded).where(Tenant.tenant_id == tenant_id)).scalar()
    return bool(result)


def _enforce(mapper, connection, target, *, op: str) -> None:
    """Block ``op`` on ``target`` unless the caller is authorized.

    Allows the write through unchanged when:
    - The parent tenant is not flagged is_embedded (open-instance tenant), or
    - The caller has set ``management_api_caller`` or ``super_admin_override``, or
    - For updates, every changed field is in the publisher-writable allow-list for
      this model.
    """
    if not _resolve_embedded_flag(target, connection):
        return

    if _caller_is_authorized(target, connection):
        return

    changed = _changed_fields(mapper, target) if op == "update" else set()
    writable = PUBLISHER_WRITABLE_FIELDS.get(type(target), set())

    # Update with no actual column changes is a relationship-only flush (e.g. a child
    # Product was added to the Tenant). Those are publisher-managed writes by definition,
    # so don't block them.
    if op == "update" and not changed:
        return

    if op == "update" and changed.issubset(writable):
        return

    detail = sorted(changed) if changed else "(insert)"
    raise EmbeddedTenantWriteError(
        f"{type(target).__name__} for tenant "
        f"{getattr(target, 'tenant_id', '?')!r} is platform-managed; "
        f"changes to {detail} must go through the Tenant Management API."
    )


@event.listens_for(Tenant, "before_update")
def _block_tenant_update(mapper, connection, target):
    _enforce(mapper, connection, target, op="update")


@event.listens_for(Tenant, "before_insert")
def _block_tenant_insert(mapper, connection, target):
    # Inserts are only blocked when the new row itself is flagged is_embedded;
    # creating a non-embedded tenant from any code path is fine.
    if not getattr(target, "is_embedded", False):
        return
    if _caller_is_authorized(target, connection):
        return
    raise EmbeddedTenantWriteError("Inserting a Tenant with is_embedded=True requires the Tenant Management API.")


@event.listens_for(AdapterConfig, "before_update")
def _block_adapter_config_update(mapper, connection, target):
    _enforce(mapper, connection, target, op="update")


@event.listens_for(AdapterConfig, "before_insert")
def _block_adapter_config_insert(mapper, connection, target):
    _enforce(mapper, connection, target, op="insert")


# Signing infrastructure (signing-non-embedded design) is platform-managed —
# per-tenant signing policy and the salesagent's own outbound signing
# credentials. Both are infrastructure surfaces; publisher UI never writes
# them on embedded tenants.
for _signing_model in (
    TenantSigningPolicy,
    TenantSigningCredential,
):

    @event.listens_for(_signing_model, "before_update")
    def _block_signing_update(mapper, connection, target):
        _enforce(mapper, connection, target, op="update")

    @event.listens_for(_signing_model, "before_insert")
    def _block_signing_insert(mapper, connection, target):
        _enforce(mapper, connection, target, op="insert")
