"""Tenant webhook events for catalog cache invalidation."""

from __future__ import annotations

from typing import Any

from src.admin.services.webhook_publisher import emit_event
from src.services.protocol_change_webhooks import notify_product_catalog_changed


def publish_product_catalog_change(
    tenant_id: str,
    *,
    action: str,
    product_id: str,
    data: dict[str, Any] | None = None,
    pricing_changed: bool = False,
    principal_ids: list[str] | None = None,
) -> None:
    payload = data or {}
    emit_product_catalog_events(
        tenant_id,
        action=action,
        product_id=product_id,
        data=payload,
        pricing_changed=pricing_changed,
    )
    notify_product_catalog_changed(
        tenant_id=tenant_id,
        action=action,
        product_id=product_id,
        data=payload,
        principal_ids=principal_ids,
    )


def emit_product_catalog_events(
    tenant_id: str,
    *,
    action: str,
    product_id: str,
    data: dict[str, Any] | None = None,
    pricing_changed: bool = False,
) -> None:
    payload = {"product_id": product_id, **(data or {})}
    event_type = {
        "created": "product.created",
        "updated": "product.updated",
        "deleted": "product.removed",
        "removed": "product.removed",
    }.get(action, "product.updated")
    emit_event(tenant_id, event_type, payload)
    if pricing_changed:
        emit_event(tenant_id, "product.priced", payload)
    _emit_bulk_change(tenant_id, affected_entity_type="product", action=action, data=payload)


def emit_signal_catalog_events(
    tenant_id: str,
    *,
    action: str,
    signal_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    payload = {"signal_id": signal_id, **(data or {})}
    event_type = {
        "created": "signal.created",
        "updated": "signal.updated",
        "deleted": "signal.removed",
        "removed": "signal.removed",
    }.get(action, "signal.updated")
    emit_event(tenant_id, event_type, payload)
    _emit_bulk_change(tenant_id, affected_entity_type="signal", action=action, data=payload)


def _emit_bulk_change(
    tenant_id: str,
    *,
    affected_entity_type: str,
    action: str,
    data: dict[str, Any],
) -> None:
    emit_event(
        tenant_id,
        "wholesale_feed.bulk_change",
        {
            "summary": f"{affected_entity_type} catalog {action}",
            "affected_count": 1,
            "affected_entity_type": affected_entity_type,
            "recommendation": "wholesale_resync",
            "change": data,
        },
    )
