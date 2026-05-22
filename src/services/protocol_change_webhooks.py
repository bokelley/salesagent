"""Protocol push notifications for long-lived account/catalog changes."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from src.core.database.repositories.push_notification import PushNotificationConfigSnapshot
from src.core.database.repositories.uow import PushNotificationUoW
from src.services.protocol_webhook_service import ProtocolWebhookService

logger = logging.getLogger(__name__)


async def notify_account_status_changed_async(
    *,
    tenant_id: str,
    account_id: str,
    from_status: str,
    to_status: str,
    principal_id: str | None = None,
) -> None:
    """Notify registered buyers that an account status changed."""
    await _notify_protocol_change_async(
        tenant_id=tenant_id,
        event_type="account.status_changed",
        object_type="account",
        object_id=account_id,
        action="status_changed",
        data={"from_status": from_status, "to_status": to_status},
        principal_id=principal_id,
    )


def notify_account_status_changed(
    *,
    tenant_id: str,
    account_id: str,
    from_status: str,
    to_status: str,
    principal_id: str | None = None,
) -> None:
    """Sync wrapper for account status notifications from Flask handlers."""
    _run_or_schedule(
        notify_account_status_changed_async(
            tenant_id=tenant_id,
            account_id=account_id,
            from_status=from_status,
            to_status=to_status,
            principal_id=principal_id,
        )
    )


def notify_product_catalog_changed(
    *,
    tenant_id: str,
    action: str,
    product_id: str,
    data: dict[str, Any] | None = None,
    principal_ids: list[str] | None = None,
) -> None:
    """Notify registered buyers that the product catalog changed."""
    _run_or_schedule(
        _notify_protocol_change_async(
            tenant_id=tenant_id,
            event_type="catalog.changed",
            object_type="product",
            object_id=product_id,
            action=action,
            refresh_tool="get_products",
            data=data or {},
            principal_ids=principal_ids,
        )
    )


def notify_signal_catalog_changed(
    *,
    tenant_id: str,
    action: str,
    signal_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Notify registered buyers that the signals catalog changed."""
    _run_or_schedule(
        _notify_protocol_change_async(
            tenant_id=tenant_id,
            event_type="catalog.changed",
            object_type="signal",
            object_id=signal_id,
            action=action,
            refresh_tool="get_signals",
            data=data or {},
        )
    )


async def _notify_protocol_change_async(
    *,
    tenant_id: str,
    event_type: str,
    object_type: str,
    object_id: str,
    action: str,
    data: dict[str, Any],
    principal_id: str | None = None,
    principal_ids: list[str] | None = None,
    refresh_tool: str | None = None,
) -> None:
    snapshots = _list_push_notification_targets(tenant_id, principal_id=principal_id)
    if principal_ids is not None:
        allowed_principals = set(principal_ids)
        snapshots = [snapshot for snapshot in snapshots if snapshot.principal_id in allowed_principals]
    if not snapshots:
        return

    timestamp = datetime.now(UTC).isoformat()
    service = ProtocolWebhookService()
    tasks = [
        service.send_notification(
            snapshot.to_delivery_config(),
            _build_change_payload(
                snapshot,
                tenant_id=tenant_id,
                event_type=event_type,
                object_type=object_type,
                object_id=object_id,
                action=action,
                refresh_tool=refresh_tool,
                data=data,
                timestamp=timestamp,
            ),
            {
                "task_type": event_type,
                "tenant_id": tenant_id,
                "principal_id": snapshot.principal_id,
            },
        )
        for snapshot in snapshots
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("protocol change webhook delivery failed", exc_info=result)


def _build_change_payload(
    snapshot: PushNotificationConfigSnapshot,
    *,
    tenant_id: str,
    event_type: str,
    object_type: str,
    object_id: str,
    action: str,
    refresh_tool: str | None,
    data: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    payload = {
        "type": event_type,
        "tenant_id": tenant_id,
        "principal_id": snapshot.principal_id,
        "object_type": object_type,
        "object_id": object_id,
        "action": action,
        "data": data,
        "timestamp": timestamp,
    }
    if refresh_tool is not None:
        payload["refresh_tool"] = refresh_tool
    if snapshot.operation_id is not None:
        payload["operation_id"] = snapshot.operation_id
    if snapshot.validation_token is not None:
        payload["token"] = snapshot.validation_token
    return payload


def _list_push_notification_targets(
    tenant_id: str, *, principal_id: str | None = None
) -> list[PushNotificationConfigSnapshot]:
    with PushNotificationUoW(tenant_id) as uow:
        assert uow.push_notifications is not None
        return uow.push_notifications.list_active_snapshots(principal_id=principal_id, purpose="catalog_changes")


def _run_or_schedule(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()
        return

    loop.create_task(coro)
