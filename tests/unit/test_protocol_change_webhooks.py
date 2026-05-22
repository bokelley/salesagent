import pytest

from src.core.database.repositories.push_notification import PushNotificationConfigSnapshot
from src.services import protocol_change_webhooks


@pytest.mark.asyncio
async def test_account_status_change_webhook_targets_registered_principal(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer.example/webhooks",
            operation_id="sync-op-1",
            authentication_type="HMAC-SHA256",
            authentication_token="shared-secret",
            validation_token="client-validation-token",
        )
    ]

    def fake_targets(tenant_id: str, *, principal_id: str | None = None):
        assert tenant_id == "tenant_1"
        assert principal_id == "agent_1"
        return snapshots

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append(
                {
                    "url": push_notification_config.url,
                    "payload": payload,
                    "metadata": metadata,
                }
            )
            return True

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", fake_targets)
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks.notify_account_status_changed_async(
        tenant_id="tenant_1",
        account_id="acc_1",
        from_status="pending_approval",
        to_status="active",
        principal_id="agent_1",
    )

    assert len(sent) == 1
    assert sent[0]["url"] == "https://buyer.example/webhooks"
    assert sent[0]["payload"]["type"] == "account.status_changed"
    assert sent[0]["payload"]["object_type"] == "account"
    assert sent[0]["payload"]["object_id"] == "acc_1"
    assert sent[0]["payload"]["data"] == {"from_status": "pending_approval", "to_status": "active"}
    assert sent[0]["payload"]["operation_id"] == "sync-op-1"
    assert sent[0]["payload"]["token"] == "client-validation-token"
    assert "validation_token" not in sent[0]["payload"]
    assert sent[0]["metadata"] == {
        "task_type": "account.status_changed",
        "tenant_id": "tenant_1",
        "principal_id": "agent_1",
    }


@pytest.mark.asyncio
async def test_catalog_change_webhook_includes_refresh_tool(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer.example/webhooks",
        )
    ]

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append({"payload": payload, "metadata": metadata})
            return True

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks._notify_protocol_change_async(
        tenant_id="tenant_1",
        event_type="catalog.changed",
        object_type="signal",
        object_id="sig_1",
        action="updated",
        refresh_tool="get_signals",
        data={"name": "Audience"},
    )

    assert sent[0]["payload"]["type"] == "catalog.changed"
    assert sent[0]["payload"]["object_type"] == "signal"
    assert sent[0]["payload"]["object_id"] == "sig_1"
    assert sent[0]["payload"]["action"] == "updated"
    assert sent[0]["payload"]["refresh_tool"] == "get_signals"
    assert sent[0]["payload"]["data"] == {"name": "Audience"}


@pytest.mark.asyncio
async def test_product_catalog_change_filters_restricted_principals(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer-1.example/webhooks",
            purpose="catalog_changes",
        ),
        PushNotificationConfigSnapshot(
            id="pnc_2",
            tenant_id="tenant_1",
            principal_id="agent_2",
            url="https://buyer-2.example/webhooks",
            purpose="catalog_changes",
        ),
    ]

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append({"url": push_notification_config.url, "payload": payload, "metadata": metadata})
            return True

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks._notify_protocol_change_async(
        tenant_id="tenant_1",
        event_type="catalog.changed",
        object_type="product",
        object_id="prod_1",
        action="updated",
        refresh_tool="get_products",
        data={"name": "Restricted Product"},
        principal_ids=["agent_2"],
    )

    assert [entry["url"] for entry in sent] == ["https://buyer-2.example/webhooks"]
