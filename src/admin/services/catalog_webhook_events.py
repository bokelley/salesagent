"""Admin-side catalog webhook emission helpers."""

from __future__ import annotations

from typing import Any

from src.admin.services.webhook_publisher import emit_event
from src.services.catalog_event_types import catalog_event_action
from src.services.protocol_change_webhooks import (
    notify_product_catalog_changed,
    notify_signal_catalog_changed,
    notify_signal_catalog_changes,
)


def normalize_catalog_acl(ids: list[str] | None) -> list[str] | None:
    """Return a stable principal ACL list, or None for unrestricted."""
    if not ids:
        return None
    return sorted(set(ids))


def catalog_acl_notification_scope(
    before: list[str] | None,
    after: list[str] | None,
) -> list[str] | None:
    """Return principals that need a catalog-change webhook for an ACL edit."""
    before = normalize_catalog_acl(before)
    after = normalize_catalog_acl(after)
    if before is None or after is None:
        return None
    return sorted(set(before) | set(after))


def publish_product_catalog_change(
    *,
    tenant_id: str,
    action: str,
    product_id: str,
    data: dict[str, Any] | None = None,
    principal_ids: list[str] | None = None,
) -> None:
    """Publish Tenant Management and protocol webhooks for one product change."""
    event_data = data or {}
    emit_event(tenant_id, f"product.{catalog_event_action(action)}", {"product_id": product_id, **event_data})
    notify_product_catalog_changed(
        tenant_id=tenant_id,
        action=action,
        product_id=product_id,
        data=event_data,
        principal_ids=principal_ids,
    )


def publish_product_record_catalog_change(*, tenant_id: str, action: str, product: Any) -> None:
    """Publish catalog webhooks for a Product-like ORM row."""
    publish_product_catalog_change(
        tenant_id=tenant_id,
        action=action,
        product_id=product.product_id,
        data={"name": product.name},
        principal_ids=product.allowed_principal_ids or None,
    )


def publish_product_record_update_catalog_change(
    *,
    tenant_id: str,
    product: Any,
    previous_allowed_principal_ids: list[str] | None,
) -> None:
    """Publish product update webhooks scoped to before/after ACL union."""
    publish_product_catalog_change(
        tenant_id=tenant_id,
        action="updated",
        product_id=product.product_id,
        data={"name": product.name},
        principal_ids=catalog_acl_notification_scope(
            previous_allowed_principal_ids,
            product.allowed_principal_ids,
        ),
    )


def publish_signal_catalog_change(
    *,
    tenant_id: str,
    action: str,
    signal_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Publish Tenant Management and protocol webhooks for one signal change."""
    event_data = data or {}
    emit_event(tenant_id, f"signal.{catalog_event_action(action)}", {"signal_id": signal_id, **event_data})
    notify_signal_catalog_changed(
        tenant_id=tenant_id,
        action=action,
        signal_id=signal_id,
        data=event_data,
    )


def publish_signal_catalog_changes(
    *,
    tenant_id: str,
    action: str,
    signal_ids: list[str],
    data: dict[str, Any] | None = None,
) -> None:
    """Publish Tenant Management and protocol webhooks for signal changes."""
    if not signal_ids:
        return
    event_data = data or {}
    event_type = f"signal.{catalog_event_action(action)}"
    for signal_id in signal_ids:
        emit_event(tenant_id, event_type, {"signal_id": signal_id, **event_data})
    notify_signal_catalog_changes(
        tenant_id=tenant_id,
        action=action,
        signal_ids=signal_ids,
        data=event_data,
    )
