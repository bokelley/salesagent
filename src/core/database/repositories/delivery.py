"""Delivery repository — tenant-scoped data access for webhook delivery tables.

Covers two ORM models:
- WebhookDeliveryRecord: webhook payload snapshots with retry tracking
- WebhookDeliveryLog: delivery report webhook sends with sequence tracking

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

Write methods add objects to the session but never commit — the caller (or UoW)
handles commit/rollback at the boundary.

beads: salesagent-7x3i
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import WebhookDeliveryLog, WebhookDeliveryRecord


class DeliveryRepository:
    """Tenant-scoped data access for WebhookDeliveryRecord and WebhookDeliveryLog.

    All queries filter by tenant_id automatically. Write methods add objects
    to the session but never commit — the Unit of Work handles that.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # WebhookDeliveryRecord reads
    # ------------------------------------------------------------------

    def get_record_by_id(self, delivery_id: str) -> WebhookDeliveryRecord | None:
        """Get a delivery record by its ID within the tenant."""
        return self._session.scalars(
            select(WebhookDeliveryRecord).where(
                WebhookDeliveryRecord.tenant_id == self._tenant_id,
                WebhookDeliveryRecord.delivery_id == delivery_id,
            )
        ).first()

    def list_records_by_tenant(
        self,
        *,
        status: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[WebhookDeliveryRecord]:
        """List delivery records for the tenant, with optional filters.

        Args:
            status: Filter by delivery status (e.g., "pending", "delivered", "failed").
            event_type: Filter by event type (e.g., "creative.status_changed").
            limit: Maximum number of records to return.
        """
        stmt = select(WebhookDeliveryRecord).where(
            WebhookDeliveryRecord.tenant_id == self._tenant_id,
        )
        if status is not None:
            stmt = stmt.where(WebhookDeliveryRecord.status == status)
        if event_type is not None:
            stmt = stmt.where(WebhookDeliveryRecord.event_type == event_type)
        stmt = stmt.order_by(WebhookDeliveryRecord.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # WebhookDeliveryRecord writes
    # ------------------------------------------------------------------

    def create_record(
        self,
        *,
        delivery_id: str,
        webhook_url: str,
        payload: dict[str, Any],
        event_type: str,
        object_id: str | None = None,
        status: str = "pending",
        attempts: int = 0,
        created_at: datetime | None = None,
    ) -> WebhookDeliveryRecord:
        """Create a new webhook delivery record.

        Does NOT commit — the caller handles that.
        """
        record = WebhookDeliveryRecord(
            delivery_id=delivery_id,
            tenant_id=self._tenant_id,
            webhook_url=webhook_url,
            payload=payload,
            event_type=event_type,
            object_id=object_id,
            status=status,
            attempts=attempts,
        )
        if created_at is not None:
            record.created_at = created_at
        self._session.add(record)
        self._session.flush()
        return record

    def update_record(
        self,
        delivery_id: str,
        *,
        status: str | None = None,
        attempts: int | None = None,
        response_code: int | None = None,
        last_error: str | None = None,
        last_attempt_at: datetime | None = None,
        delivered_at: datetime | None = None,
    ) -> WebhookDeliveryRecord | None:
        """Update fields on a delivery record within this tenant.

        Returns the updated record, or None if not found.
        Does NOT commit — the caller handles that.
        """
        record = self.get_record_by_id(delivery_id)
        if record is None:
            return None
        if status is not None:
            record.status = status
        if attempts is not None:
            record.attempts = attempts
        if response_code is not None:
            record.response_code = response_code
        if last_error is not None:
            record.last_error = last_error
        if last_attempt_at is not None:
            record.last_attempt_at = last_attempt_at
        if delivered_at is not None:
            record.delivered_at = delivered_at
        self._session.flush()
        return record

    # ------------------------------------------------------------------
    # WebhookDeliveryLog reads
    # ------------------------------------------------------------------

    def get_logs_by_webhook_id(
        self,
        media_buy_id: str,
        *,
        task_type: str | None = None,
        status: str | None = None,
    ) -> list[WebhookDeliveryLog]:
        """Get delivery logs for a media buy within the tenant.

        Args:
            media_buy_id: The media buy to get logs for.
            task_type: Filter by task type (e.g., "media_buy_delivery").
            status: Filter by log status (e.g., "success", "failed").
        """
        stmt = select(WebhookDeliveryLog).where(
            WebhookDeliveryLog.tenant_id == self._tenant_id,
            WebhookDeliveryLog.media_buy_id == media_buy_id,
        )
        if task_type is not None:
            stmt = stmt.where(WebhookDeliveryLog.task_type == task_type)
        if status is not None:
            stmt = stmt.where(WebhookDeliveryLog.status == status)
        stmt = stmt.order_by(WebhookDeliveryLog.created_at.desc())
        return list(self._session.scalars(stmt).all())

    def get_recent_successful_log(
        self,
        media_buy_id: str,
        *,
        task_type: str,
        notification_type: str,
        since: datetime,
    ) -> WebhookDeliveryLog | None:
        """Find a recent successful log entry (for duplicate detection).

        Used by the scheduler to check if a report was already sent.
        """
        return self._session.scalars(
            select(WebhookDeliveryLog).where(
                WebhookDeliveryLog.tenant_id == self._tenant_id,
                WebhookDeliveryLog.media_buy_id == media_buy_id,
                WebhookDeliveryLog.task_type == task_type,
                WebhookDeliveryLog.notification_type == notification_type,
                WebhookDeliveryLog.status == "success",
                WebhookDeliveryLog.created_at > since,
            )
        ).first()

    def get_max_sequence_number(
        self,
        media_buy_id: str,
        *,
        task_type: str,
    ) -> int:
        """Get the maximum sequence number for a media buy's delivery logs.

        Returns 0 if no logs exist (caller should add 1 for the next sequence).
        """
        result = self._session.scalar(
            select(func.coalesce(func.max(WebhookDeliveryLog.sequence_number), 0)).where(
                WebhookDeliveryLog.tenant_id == self._tenant_id,
                WebhookDeliveryLog.media_buy_id == media_buy_id,
                WebhookDeliveryLog.task_type == task_type,
            )
        )
        return result or 0

    # ------------------------------------------------------------------
    # WebhookDeliveryLog writes
    # ------------------------------------------------------------------

    # Maximum size in bytes for stored request_payload (JSON-encoded) and
    # response_body. Anything larger is truncated at insert time so a
    # pathologically-large webhook body can't blow up the row.
    _BODY_TRUNCATION_BYTES: int = 64 * 1024

    @classmethod
    def _truncate_request_payload(cls, payload: dict | None) -> dict | None:
        """Truncate or replace a JSON payload to ~64KB.

        Shape contract for buyer-visible consumers:
        - Returns the original dict unchanged if it fits and is
          serializable (this is the common case — buyers see exactly the
          AdCP webhook body we sent).
        - On overflow / unserializable input, returns a sentinel dict
          with a single ``_meta`` key:
          ``{"_meta": {"truncated": True, "original_size_bytes": N,
          "preview": <first 4KB of stringified payload>}}``
          so consumers can distinguish "real payload" from "metadata
          replacement" by checking ``_meta`` membership rather than
          guessing from leaked underscore-prefixed keys.
        """
        if payload is None:
            return None
        import json

        try:
            encoded = json.dumps(payload)
        except (TypeError, ValueError):
            return {"_meta": {"truncated": True, "reason": "unserializable"}}
        size = len(encoded.encode("utf-8"))
        if size <= cls._BODY_TRUNCATION_BYTES:
            return payload
        return {
            "_meta": {
                "truncated": True,
                "original_size_bytes": size,
                "preview": encoded[:4096],
            }
        }

    @classmethod
    def _truncate_response_body(cls, body: str | None) -> str | None:
        """Truncate a response body to ~64KB.

        Plain ASCII / valid UTF-8 bodies get sliced at the byte boundary
        with a ``[truncated]`` marker appended. If the body is not
        decodable as UTF-8 (binary error pages, gzipped HTML, etc.)
        we replace the whole field with a sentinel string rather than
        silently producing a partially-stripped garbage string from
        ``decode(errors='ignore')``.
        """
        if body is None:
            return None
        encoded = body.encode("utf-8")
        size = len(encoded)
        if size <= cls._BODY_TRUNCATION_BYTES:
            return body
        # Try to slice at the byte boundary and decode strictly. If the
        # cut lands mid-multibyte, walk back up to 3 bytes until we
        # find a valid prefix. If nothing decodes (rare — implies
        # invalid UTF-8 in the original body too), fall through to the
        # binary sentinel.
        for end in range(cls._BODY_TRUNCATION_BYTES, max(0, cls._BODY_TRUNCATION_BYTES - 4), -1):
            try:
                prefix = encoded[:end].decode("utf-8")
                return prefix + "\n... [truncated]"
            except UnicodeDecodeError:
                continue
        return f"<binary response, {size} bytes, not stored>"

    def create_log(
        self,
        *,
        log_id: str,
        principal_id: str,
        media_buy_id: str,
        webhook_url: str,
        task_type: str,
        status: str,
        attempt_count: int = 1,
        sequence_number: int = 1,
        notification_type: str | None = None,
        http_status_code: int | None = None,
        error_message: str | None = None,
        payload_size_bytes: int | None = None,
        response_time_ms: int | None = None,
        completed_at: datetime | None = None,
        next_retry_at: datetime | None = None,
        request_payload: dict | None = None,
        response_body: str | None = None,
    ) -> WebhookDeliveryLog:
        """Create or update a webhook delivery log entry.

        Uses session.merge() to handle upsert semantics (the protocol webhook
        service updates the same log entry across retry attempts).

        ``request_payload`` and ``response_body`` are truncated to ~64KB
        on insert so a pathologically-large webhook body can't blow up
        the row. Pass them when you have them; older callers that don't
        provide them get ``None`` (rows pre-#101 schema).

        Does NOT commit — the caller handles that.
        """
        log_entry = WebhookDeliveryLog(
            id=log_id,
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            webhook_url=webhook_url,
            task_type=task_type,
            status=status,
            attempt_count=attempt_count,
            sequence_number=sequence_number,
            notification_type=notification_type,
            http_status_code=http_status_code,
            error_message=error_message,
            payload_size_bytes=payload_size_bytes,
            response_time_ms=response_time_ms,
            completed_at=completed_at,
            next_retry_at=next_retry_at,
            request_payload=self._truncate_request_payload(request_payload),
            response_body=self._truncate_response_body(response_body),
        )
        self._session.merge(log_entry)
        self._session.flush()
        return log_entry

    # ------------------------------------------------------------------
    # WebhookDeliveryLog cross-tenant maintenance (retention)
    # ------------------------------------------------------------------

    @staticmethod
    def delete_logs_older_than(session: Session, cutoff: datetime) -> int:
        """Delete every webhook_delivery_log row with ``created_at < cutoff``.

        Tenant-agnostic — used by the retention script which prunes
        across the whole table. Returns the rowcount of the DELETE.
        Caller commits.
        """
        from sqlalchemy import delete as sa_delete

        result = session.execute(sa_delete(WebhookDeliveryLog).where(WebhookDeliveryLog.created_at < cutoff))
        # CursorResult.rowcount is set after a DELETE on the SA 2.0 dialect.
        rowcount = getattr(result, "rowcount", 0) or 0
        return int(rowcount)

    @staticmethod
    def count_logs_older_than(session: Session, cutoff: datetime) -> int:
        """Count rows that ``delete_logs_older_than`` would delete.

        Used for ``--dry-run`` reporting in the retention script.
        Tenant-agnostic.
        """
        from sqlalchemy import func as sa_func

        return int(
            session.execute(
                select(sa_func.count()).select_from(WebhookDeliveryLog).where(WebhookDeliveryLog.created_at < cutoff)
            ).scalar_one()
        )

    # ------------------------------------------------------------------
    # WebhookDeliveryLog reads — for #101 buyer self-debug surface
    # ------------------------------------------------------------------

    def _list_logs_query(self, media_buy_id: str, limit: int):
        return (
            select(WebhookDeliveryLog)
            .where(WebhookDeliveryLog.tenant_id == self._tenant_id)
            .where(WebhookDeliveryLog.media_buy_id == media_buy_id)
            .order_by(WebhookDeliveryLog.created_at.desc())
            .limit(limit)
        )

    def list_logs_for_buyer(
        self,
        media_buy_id: str,
        principal_id: str,
        *,
        limit: int = 50,
    ) -> list[WebhookDeliveryLog]:
        """Recent webhook deliveries for a media buy, scoped to one principal.

        Use on buyer-facing surfaces. ``principal_id`` is required so a
        buyer agent never sees another principal's deliveries even if
        multiple agents share visibility into the same buy.
        """
        stmt = self._list_logs_query(media_buy_id, limit).where(WebhookDeliveryLog.principal_id == principal_id)
        return list(self._session.scalars(stmt).all())

    def list_logs_for_operator(
        self,
        media_buy_id: str,
        *,
        limit: int = 50,
    ) -> list[WebhookDeliveryLog]:
        """Recent webhook deliveries for a media buy across all principals.

        Tenant-scoped. Use only on operator-facing surfaces (admin UI)
        where the caller is authorized to see all webhook activity for
        every principal in the tenant.
        """
        return list(self._session.scalars(self._list_logs_query(media_buy_id, limit)).all())
