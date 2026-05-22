"""Registration helpers for protocol push-notification webhooks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from src.core.database.repositories.push_notification import PushNotificationConfigRepository
from src.core.database.repositories.uow import PushNotificationUoW


@dataclass(frozen=True)
class PushNotificationRegistration:
    """Normalized values stored for a protocol webhook registration."""

    config_id: str | None
    url: str
    operation_id: str | None = None
    authentication_type: str | None = None
    authentication_token: str | None = None
    validation_token: str | None = None
    signing_mode: str = "hmac"


def normalize_push_notification_config(config: Any) -> PushNotificationRegistration | None:
    """Normalize AdCP PushNotificationConfig into DB-storable values."""
    config_dict = _config_to_dict(config)
    if not config_dict:
        return None

    url = config_dict.get("url")
    if url is None:
        return None

    authentication = config_dict.get("authentication") or {}
    schemes = authentication.get("schemes") or []
    auth_type = str(schemes[0]) if schemes else None
    credentials = authentication.get("credentials")
    config_id = str(config_dict["id"]) if config_dict.get("id") is not None else None
    return PushNotificationRegistration(
        config_id=config_id,
        url=str(url),
        operation_id=str(config_dict["operation_id"]) if config_dict.get("operation_id") is not None else None,
        authentication_type=auth_type,
        authentication_token=str(credentials) if credentials is not None else None,
        validation_token=str(config_dict["token"]) if config_dict.get("token") is not None else None,
        signing_mode="hmac",
    )


def register_push_notification_config(
    tenant_id: str,
    principal_id: str,
    config: Any,
    *,
    session_id: str | None = None,
) -> PushNotificationRegistration | None:
    """Persist a protocol webhook registration in its own UoW."""
    registration = normalize_push_notification_config(config)
    if registration is None:
        return None

    with PushNotificationUoW(tenant_id) as uow:
        assert uow.push_notifications is not None
        register_push_notification_config_in_repo(
            uow.push_notifications,
            principal_id=principal_id,
            registration=registration,
            session_id=session_id,
        )
    return registration


def register_push_notification_config_in_repo(
    repo: PushNotificationConfigRepository,
    *,
    principal_id: str,
    registration: PushNotificationRegistration,
    session_id: str | None = None,
) -> None:
    """Persist an already-normalized registration using the caller's repository."""
    repo.upsert(
        config_id=registration.config_id
        or _stable_config_id(tenant_id=repo.tenant_id, principal_id=principal_id, url=registration.url),
        principal_id=principal_id,
        url=registration.url,
        operation_id=registration.operation_id,
        authentication_type=registration.authentication_type,
        authentication_token=registration.authentication_token,
        validation_token=registration.validation_token,
        session_id=session_id,
        purpose="catalog_changes",
        signing_mode=registration.signing_mode,
    )


def _config_to_dict(config: Any) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, dict):
        return config
    if hasattr(config, "model_dump"):
        return config.model_dump(mode="json", exclude_none=True)
    return dict(config)


def _stable_config_id(*, tenant_id: str, principal_id: str, url: str) -> str:
    """Derive an idempotent registration id for SDK configs that do not carry one."""
    digest = hashlib.sha256(f"{tenant_id}\0{principal_id}\0{url}".encode()).hexdigest()
    return f"pnc_{digest[:16]}"
