from __future__ import annotations

from types import SimpleNamespace

from src.admin.services import catalog_webhook_events


def test_publish_product_catalog_change_emits_tenant_and_protocol_webhooks(monkeypatch) -> None:
    emitted = []
    protocol = []
    monkeypatch.setattr(catalog_webhook_events, "emit_event", lambda *args: emitted.append(args))
    monkeypatch.setattr(
        catalog_webhook_events,
        "notify_product_catalog_changed",
        lambda **kwargs: protocol.append(kwargs),
    )

    catalog_webhook_events.publish_product_catalog_change(
        tenant_id="tenant_1",
        action="deleted",
        product_id="prod_1",
        data={"name": "Homepage"},
        principal_ids=["principal_1"],
    )

    assert emitted == [
        ("tenant_1", "product.removed", {"product_id": "prod_1", "name": "Homepage"}),
        (
            "tenant_1",
            "wholesale_feed.bulk_change",
            {
                "summary": "product catalog deleted",
                "affected_count": 1,
                "affected_entity_type": "product",
                "recommendation": "wholesale_resync",
                "change": {"product_id": "prod_1", "name": "Homepage"},
            },
        ),
    ]
    assert protocol == [
        {
            "tenant_id": "tenant_1",
            "action": "deleted",
            "product_id": "prod_1",
            "data": {"name": "Homepage"},
            "principal_ids": ["principal_1"],
        }
    ]


def test_publish_signal_catalog_change_emits_tenant_and_protocol_webhooks(monkeypatch) -> None:
    emitted = []
    protocol = []
    monkeypatch.setattr(catalog_webhook_events, "emit_event", lambda *args: emitted.append(args))
    monkeypatch.setattr(
        catalog_webhook_events,
        "notify_signal_catalog_changed",
        lambda **kwargs: protocol.append(kwargs),
    )

    catalog_webhook_events.publish_signal_catalog_change(
        tenant_id="tenant_1",
        action="deleted",
        signal_id="sig_1",
        data={"name": "Audience"},
    )

    assert emitted == [
        ("tenant_1", "signal.removed", {"signal_id": "sig_1", "name": "Audience"}),
        (
            "tenant_1",
            "wholesale_feed.bulk_change",
            {
                "summary": "signal catalog deleted",
                "affected_count": 1,
                "affected_entity_type": "signal",
                "recommendation": "wholesale_resync",
                "change": {"signal_id": "sig_1", "name": "Audience"},
            },
        ),
    ]
    assert protocol == [
        {"tenant_id": "tenant_1", "action": "deleted", "signal_id": "sig_1", "data": {"name": "Audience"}}
    ]


def test_publish_product_record_catalog_change_uses_row_identity(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(catalog_webhook_events, "publish_product_catalog_change", lambda **kwargs: calls.append(kwargs))
    product = SimpleNamespace(
        product_id="prod_1",
        name="Homepage",
        allowed_principal_ids=["principal_1"],
    )

    catalog_webhook_events.publish_product_record_catalog_change(
        tenant_id="tenant_1",
        action="updated",
        product=product,
    )

    assert calls == [
        {
            "tenant_id": "tenant_1",
            "action": "updated",
            "product_id": "prod_1",
            "data": {"name": "Homepage"},
            "principal_ids": ["principal_1"],
        }
    ]


def test_publish_product_record_update_catalog_change_uses_acl_union(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(catalog_webhook_events, "publish_product_catalog_change", lambda **kwargs: calls.append(kwargs))
    product = SimpleNamespace(
        product_id="prod_1",
        name="Homepage",
        allowed_principal_ids=["new_principal"],
    )

    catalog_webhook_events.publish_product_record_update_catalog_change(
        tenant_id="tenant_1",
        product=product,
        previous_allowed_principal_ids=["old_principal"],
    )

    assert calls == [
        {
            "tenant_id": "tenant_1",
            "action": "updated",
            "product_id": "prod_1",
            "data": {"name": "Homepage"},
            "principal_ids": ["new_principal", "old_principal"],
            "pricing_changed": False,
        }
    ]


def test_publish_signal_catalog_changes_emits_each_tenant_event_once(monkeypatch) -> None:
    emitted = []
    protocol = []
    monkeypatch.setattr(catalog_webhook_events, "emit_event", lambda *args: emitted.append(args))
    monkeypatch.setattr(
        catalog_webhook_events,
        "notify_signal_catalog_changes",
        lambda **kwargs: protocol.append(kwargs),
    )

    catalog_webhook_events.publish_signal_catalog_changes(
        tenant_id="tenant_1",
        action="created",
        signal_ids=["sig_1", "sig_2"],
    )

    assert emitted == [
        ("tenant_1", "signal.created", {"signal_id": "sig_1"}),
        ("tenant_1", "signal.created", {"signal_id": "sig_2"}),
        (
            "tenant_1",
            "wholesale_feed.bulk_change",
            {
                "summary": "signal catalog created",
                "affected_count": 2,
                "affected_entity_type": "signal",
                "recommendation": "wholesale_resync",
                "change": {"signal_ids": ["sig_1", "sig_2"]},
            },
        ),
    ]
    assert protocol == [{"tenant_id": "tenant_1", "action": "created", "signal_ids": ["sig_1", "sig_2"], "data": {}}]
