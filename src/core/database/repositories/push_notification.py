"""Repository for protocol push-notification webhook registrations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import PushNotificationConfig


@dataclass(frozen=True)
class PushNotificationConfigSnapshot:
    """Detached delivery target for sending webhooks after DB session close."""

    id: str
    tenant_id: str
    principal_id: str
    url: str
    operation_id: str | None = None
    authentication_type: str | None = None
    authentication_token: str | None = None
    validation_token: str | None = None
    webhook_secret: str | None = None
    purpose: str = "async_task"
    signing_mode: str = "hmac"

    def to_delivery_config(self) -> PushNotificationConfig:
        """Return an unsessioned ORM-shaped object for the delivery service."""
        return PushNotificationConfig(
            id=self.id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            url=self.url,
            operation_id=self.operation_id,
            authentication_type=self.authentication_type,
            authentication_token=self.authentication_token,
            validation_token=self.validation_token,
            webhook_secret=self.webhook_secret,
            purpose=self.purpose,
            signing_mode=self.signing_mode,
            is_active=True,
        )


class PushNotificationConfigRepository:
    """Tenant-scoped access to protocol webhook registrations."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def upsert(
        self,
        *,
        config_id: str,
        principal_id: str,
        url: str,
        operation_id: str | None = None,
        authentication_type: str | None = None,
        authentication_token: str | None = None,
        validation_token: str | None = None,
        session_id: str | None = None,
        webhook_secret: str | None = None,
        purpose: str = "async_task",
        signing_mode: str = "hmac",
    ) -> PushNotificationConfig:
        """Create or update a principal's protocol webhook registration."""
        config = self._session.scalars(
            select(PushNotificationConfig).where(
                PushNotificationConfig.id == config_id,
                PushNotificationConfig.tenant_id == self._tenant_id,
                PushNotificationConfig.principal_id == principal_id,
            )
        ).first()

        if config is None:
            config = PushNotificationConfig(
                id=config_id,
                tenant_id=self._tenant_id,
                principal_id=principal_id,
                url=url,
                operation_id=operation_id,
                authentication_type=authentication_type,
                authentication_token=authentication_token,
                validation_token=validation_token,
                session_id=session_id,
                webhook_secret=webhook_secret,
                purpose=purpose,
                signing_mode=signing_mode,
                is_active=True,
            )
            self._session.add(config)
        else:
            config.url = url
            config.operation_id = operation_id
            config.authentication_type = authentication_type
            config.authentication_token = authentication_token
            config.validation_token = validation_token
            config.session_id = session_id
            config.webhook_secret = webhook_secret
            config.purpose = purpose
            config.signing_mode = signing_mode
            config.is_active = True

        self._session.flush()
        return config

    def list_active(
        self, *, principal_id: str | None = None, purpose: str | None = None
    ) -> list[PushNotificationConfig]:
        """List active registrations for a tenant, optionally scoped to one principal."""
        stmt = select(PushNotificationConfig).where(
            PushNotificationConfig.tenant_id == self._tenant_id,
            PushNotificationConfig.is_active.is_(True),
        )
        if principal_id is not None:
            stmt = stmt.where(PushNotificationConfig.principal_id == principal_id)
        if purpose is not None:
            stmt = stmt.where(PushNotificationConfig.purpose == purpose)
        return list(self._session.scalars(stmt).all())

    def list_active_snapshots(
        self, *, principal_id: str | None = None, purpose: str | None = None
    ) -> list[PushNotificationConfigSnapshot]:
        """List active registrations as detached snapshots."""
        return [
            PushNotificationConfigSnapshot(
                id=config.id,
                tenant_id=config.tenant_id,
                principal_id=config.principal_id,
                url=config.url,
                operation_id=config.operation_id,
                authentication_type=config.authentication_type,
                authentication_token=config.authentication_token,
                validation_token=config.validation_token,
                webhook_secret=config.webhook_secret,
                purpose=config.purpose,
                signing_mode=config.signing_mode,
            )
            for config in self.list_active(principal_id=principal_id, purpose=purpose)
        ]
