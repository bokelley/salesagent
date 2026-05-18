"""WebhookSubscription repository — tenant-scoped CRUD for outbound webhooks.

[embedded-mode](../../../../docs/design/embedded-mode.md):
the Tenant Management API exposes ``/tenants/{tid}/webhooks`` so host products
can register destinations for tenant lifecycle events. All access goes through
this repository so the structural guard (``test_architecture_no_raw_select.py``)
keeps holding when new endpoints are added.

Tenant scoping: every query filters by ``tenant_id`` set at construction.
Soft-delete: ``deactivate`` flips ``is_active=false`` rather than hard-deleting,
preserving the row for audit references.
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import WebhookSubscription


def hash_secret(secret: str) -> str:
    """Return the sha256 hex digest used for secret_hash storage.

    The plaintext is never stored — receivers verify HMAC signatures using
    the plaintext we returned at create time.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_secret() -> str:
    """Generate a fresh subscription secret.

    Length: 64 hex chars (32 bytes of entropy). Comfortably above the 32-char
    minimum the SDK's ``deliver()`` enforces for HMAC-SHA256 credentials.
    """
    return secrets.token_hex(32)


class WebhookSubscriptionRepository:
    """Tenant-scoped CRUD against the ``webhook_subscriptions`` table.

    Args:
        session: Active SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope. Every query filters on this id.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_active(self) -> list[WebhookSubscription]:
        """List active subscriptions for the tenant, oldest-first.

        Used by the ``GET /webhooks`` endpoint and by the event publisher
        to find subscribers for a given event type.
        """
        stmt = (
            select(WebhookSubscription)
            .where(
                WebhookSubscription.tenant_id == self._tenant_id,
                WebhookSubscription.is_active.is_(True),
            )
            .order_by(WebhookSubscription.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def list_for_event(self, event_type: str) -> list[WebhookSubscription]:
        """Return active subscriptions interested in ``event_type``.

        Empty ``event_types`` means "all events" — those subscriptions match
        every event type.
        """
        return [sub for sub in self.list_active() if not sub.event_types or event_type in sub.event_types]

    def get_by_id(self, webhook_id: str, *, include_inactive: bool = False) -> WebhookSubscription | None:
        """Return the subscription with this id, or None.

        Active-only by default — ``include_inactive=True`` returns soft-deleted
        rows (used by audit drill-downs that need to resolve historical
        webhook ids).
        """
        stmt = select(WebhookSubscription).where(
            WebhookSubscription.tenant_id == self._tenant_id,
            WebhookSubscription.webhook_id == webhook_id,
        )
        if not include_inactive:
            stmt = stmt.where(WebhookSubscription.is_active.is_(True))
        return self._session.scalars(stmt).first()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        webhook_id: str,
        url: str,
        event_types: list[str],
        secret: str,
        description: str | None = None,
        extra_headers: dict | None = None,
    ) -> WebhookSubscription:
        """Insert a new subscription. Caller commits.

        Stores ``hash_secret(secret)`` — the plaintext is the API caller's
        responsibility to surface to the receiver. There is no read path
        for the plaintext after this point.
        """
        sub = WebhookSubscription(
            webhook_id=webhook_id,
            tenant_id=self._tenant_id,
            url=url,
            event_types=list(event_types),
            description=description,
            secret_hash=hash_secret(secret),
            extra_headers=extra_headers,
            is_active=True,
            consecutive_failures=0,
        )
        self._session.add(sub)
        return sub

    def deactivate(self, sub: WebhookSubscription) -> None:
        """Soft-delete: flip ``is_active=false``. Caller commits.

        Preserves the row so audit references and dead-letter records
        retain their FK target.
        """
        sub.is_active = False

    def record_delivery(
        self,
        sub: WebhookSubscription,
        *,
        status_code: int | None,
        success: bool,
        now,
    ) -> None:
        """Update delivery stats after a send attempt. Caller commits.

        ``consecutive_failures`` resets on success and increments on failure;
        the ``/webhooks/{wid}/test`` endpoint and the event publisher both
        feed through here so the disablement counter stays consistent.
        """
        sub.last_delivery_at = now
        sub.last_delivery_status = status_code
        if success:
            sub.consecutive_failures = 0
        else:
            sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
