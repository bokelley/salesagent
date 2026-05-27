from src.admin.services.catalog_webhook_events import catalog_acl_notification_scope


def test_catalog_acl_scope_treats_empty_acl_as_unrestricted() -> None:
    assert catalog_acl_notification_scope([], ["buyer_1"]) is None
    assert catalog_acl_notification_scope(["buyer_1"], []) is None


def test_catalog_acl_scope_includes_removed_and_added_principals() -> None:
    assert catalog_acl_notification_scope(["buyer_1", "buyer_2"], ["buyer_2", "buyer_3"]) == [
        "buyer_1",
        "buyer_2",
        "buyer_3",
    ]
