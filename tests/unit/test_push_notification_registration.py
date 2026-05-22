from src.services.push_notification_registration import (
    normalize_push_notification_config,
    register_push_notification_config_in_repo,
)


class RecordingPushNotificationRepo:
    tenant_id = "tenant_1"

    def __init__(self) -> None:
        self.calls = []

    def upsert(self, **kwargs):
        self.calls.append(kwargs)


def test_normalizes_legacy_authenticated_push_notification_config() -> None:
    registration = normalize_push_notification_config(
        {
            "id": "pnc_sync_accounts",
            "url": "https://buyer.example/webhooks",
            "operation_id": "sync-op-1",
            "token": "client-validation-token",
            "authentication": {
                "schemes": ["HMAC-SHA256"],
                "credentials": "shared-secret",
            },
        }
    )

    assert registration is not None
    assert registration.config_id == "pnc_sync_accounts"
    assert registration.url == "https://buyer.example/webhooks"
    assert registration.operation_id == "sync-op-1"
    assert registration.authentication_type == "HMAC-SHA256"
    assert registration.authentication_token == "shared-secret"
    assert registration.validation_token == "client-validation-token"
    assert registration.signing_mode == "hmac"


def test_register_push_notification_config_in_repo_upserts_normalized_values() -> None:
    repo = RecordingPushNotificationRepo()
    registration = normalize_push_notification_config(
        {
            "id": "pnc_sync_accounts",
            "url": "https://buyer.example/webhooks",
            "operation_id": "sync-op-2",
            "authentication": {"schemes": ["Bearer"], "credentials": "bearer-token"},
        }
    )

    assert registration is not None
    register_push_notification_config_in_repo(
        repo,
        principal_id="agent_1",
        registration=registration,
        session_id="session_1",
    )

    assert repo.calls == [
        {
            "config_id": "pnc_sync_accounts",
            "principal_id": "agent_1",
            "url": "https://buyer.example/webhooks",
            "operation_id": "sync-op-2",
            "authentication_type": "Bearer",
            "authentication_token": "bearer-token",
            "validation_token": None,
            "session_id": "session_1",
            "purpose": "catalog_changes",
            "signing_mode": "hmac",
        }
    ]


def test_sdk_push_notification_config_gets_stable_generated_id() -> None:
    config = {
        "url": "https://buyer.example/webhooks",
        "operation_id": "catalog-refresh-1",
        "token": "client-validation-token",
    }

    first = normalize_push_notification_config(config)
    second = normalize_push_notification_config(config)

    assert first is not None
    assert second is not None
    assert first.config_id is None
    assert second.config_id is None


def test_generated_id_is_stable_per_principal_url_not_operation() -> None:
    repo = RecordingPushNotificationRepo()
    first = normalize_push_notification_config(
        {
            "url": "https://buyer.example/webhooks",
            "operation_id": "catalog-refresh-1",
        }
    )
    second = normalize_push_notification_config(
        {
            "url": "https://buyer.example/webhooks",
            "operation_id": "catalog-refresh-2",
        }
    )

    assert first is not None
    assert second is not None
    register_push_notification_config_in_repo(repo, principal_id="agent_1", registration=first)
    register_push_notification_config_in_repo(repo, principal_id="agent_1", registration=second)

    assert repo.calls[0]["config_id"] == repo.calls[1]["config_id"]
    assert repo.calls[0]["config_id"].startswith("pnc_")
    assert repo.calls[1]["operation_id"] == "catalog-refresh-2"


def test_generated_id_is_scoped_by_principal() -> None:
    repo = RecordingPushNotificationRepo()
    registration = normalize_push_notification_config({"url": "https://buyer.example/webhooks"})

    assert registration is not None
    register_push_notification_config_in_repo(repo, principal_id="agent_1", registration=registration)
    register_push_notification_config_in_repo(repo, principal_id="agent_2", registration=registration)

    assert repo.calls[0]["config_id"] != repo.calls[1]["config_id"]
