"""Audit log repository — tenant-scoped reads + write helper.

[embedded-mode](../../../../docs/design/embedded-mode.md):
the Tenant Management API surfaces audit log entries via
``GET /tenants/{tid}/audit-log``. All access goes through this repository so
the structural guard (``test_architecture_no_raw_select.py``) keeps holding
when new endpoints are added.

Tenant scoping: every query filters by ``tenant_id`` set at construction.
Writes are session-add only; the caller commits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.core.database.models import AuditLog


class AuditLogRepository:
    """Tenant-scoped reads/writes against the ``audit_logs`` table.

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

    def list_filtered(
        self,
        *,
        action_prefix: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        actor_type: str | None = None,
        external_source: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        cursor_timestamp: datetime | None = None,
        cursor_id: int | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        """List audit log rows for the tenant, ordered by ``timestamp desc, log_id desc``.

        ``action_prefix`` matches the ``operation`` column as a dotted-name
        prefix (e.g. ``workflow.`` matches ``workflow.approve``).

        ``subject_type``/``subject_id`` are not stored as columns; they live
        in the ``details`` JSON object the writer populated. Postgres JSON
        operators are used for the prefix match.

        Cursor pagination uses ``(timestamp, log_id)`` so concurrent inserts
        with the same timestamp don't get skipped or duplicated.
        """
        stmt = select(AuditLog).where(AuditLog.tenant_id == self._tenant_id)

        if action_prefix:
            # Dotted-prefix match. ``startswith`` translates to ``LIKE 'prefix%'``.
            stmt = stmt.where(AuditLog.operation.startswith(action_prefix))
        if subject_type:
            stmt = stmt.where(AuditLog.details["subject_type"].as_string() == subject_type)
        if subject_id:
            stmt = stmt.where(AuditLog.details["subject_id"].as_string() == subject_id)
        if actor_type:
            stmt = stmt.where(AuditLog.details["actor_type"].as_string() == actor_type)
        if external_source:
            stmt = stmt.where(AuditLog.external_source == external_source)
        if from_date:
            stmt = stmt.where(AuditLog.timestamp >= from_date)
        if to_date:
            stmt = stmt.where(AuditLog.timestamp <= to_date)

        # Cursor: rows strictly older than (cursor_timestamp, cursor_id) by
        # the (timestamp desc, log_id desc) ordering — i.e. timestamp <
        # cursor_timestamp, OR (timestamp == cursor_timestamp AND log_id <
        # cursor_id). Compare on the tuple to avoid skipping ties.
        if cursor_timestamp is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    AuditLog.timestamp < cursor_timestamp,
                    and_(
                        AuditLog.timestamp == cursor_timestamp,
                        AuditLog.log_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(AuditLog.timestamp.desc(), AuditLog.log_id.desc()).limit(limit)
        return list(self._session.scalars(stmt).all())

    def last_signed_verification_for_principal(self, principal_id: str) -> AuditLog | None:
        """Most recent audit row for ``principal_id`` that recorded a
        successful RFC 9421 verification (``verified_agent_url`` is set).

        Used by the buyer-agent admin page to surface "🟢 verified <Nm ago>"
        status before the operator flips ``signing_required=true``. Returns
        ``None`` if no signed request from this principal has been verified
        yet.
        """
        stmt = (
            select(AuditLog)
            .where(AuditLog.tenant_id == self._tenant_id)
            .where(AuditLog.principal_id == principal_id)
            .where(AuditLog.verified_agent_url.is_not(None))
            .order_by(AuditLog.timestamp.desc(), AuditLog.log_id.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        operation: str,
        subject_type: str,
        subject_id: str,
        actor_type: str,
        success: bool = True,
        principal_id: str | None = None,
        principal_name: str | None = None,
        external_user_email: str | None = None,
        external_user_id: str | None = None,
        external_org_id: str | None = None,
        external_source: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
        verified_agent_url: str | None = None,
        verified_key_id: str | None = None,
    ) -> AuditLog:
        """Add an audit log row to the session for this tenant.

        Subject metadata (``subject_type``/``subject_id``) and ``actor_type``
        live in the ``details`` JSON column — there are no dedicated columns.
        Read endpoints filter on the same JSON keys.

        ``verified_*`` populate the RFC 9421 signed-request trail (PR 2D of
        signing-non-embedded). All NULL on rows for unsigned requests.
        """
        merged_details = dict(details or {})
        merged_details.setdefault("subject_type", subject_type)
        merged_details.setdefault("subject_id", subject_id)
        merged_details.setdefault("actor_type", actor_type)

        entry = AuditLog(
            tenant_id=self._tenant_id,
            operation=operation,
            principal_id=principal_id,
            principal_name=principal_name,
            success=success,
            error_message=error_message,
            details=merged_details,
            external_user_email=external_user_email,
            external_user_id=external_user_id,
            external_org_id=external_org_id,
            external_source=external_source,
            verified_agent_url=verified_agent_url,
            verified_key_id=verified_key_id,
        )
        self._session.add(entry)
        return entry
