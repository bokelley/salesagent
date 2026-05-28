"""Sprint 3 endpoint helpers for the Tenant Management API.

[embedded-mode](../../../../docs/design/embedded-mode.md)
adds workflow approve/reject + read drill-down endpoints (workflows,
media-buys, audit log, sync history) to ``src/admin/tenant_management_api.py``.
The serializers, cursor helpers, and decision-recording logic live here so
the main blueprint module stays focused on routing.

All persistence goes through the repository layer. Cursor pagination uses
``(timestamp, id)`` tuples encoded as opaque base64 — survives concurrent
inserts in a way offset pagination can't.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

from src.admin.api_schemas.tenant_management import (
    AuditLogEntry,
    MediaBuyDetail,
    MediaBuySummary,
    StatusEvent,
    SyncRunInfo,
    WorkflowDecision,
    WorkflowDetail,
    WorkflowSummary,
)
from src.core.database.models import AuditLog, MediaBuy, SyncJob, WorkflowStep

# ---------------------------------------------------------------------------
# Cursor pagination helpers
# ---------------------------------------------------------------------------


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode a cursor payload as opaque base64.

    Callers pass plain JSON-serializable dicts; we serialize datetimes via
    isoformat() so the payload survives a roundtrip.
    """
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, datetime):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return base64.urlsafe_b64encode(json.dumps(safe).encode()).decode()


def decode_cursor(raw: str | None) -> dict[str, Any]:
    """Decode an opaque cursor.

    Invalid / empty cursors yield an empty dict — match the established
    cursor-error behavior for the GAM advertisers list endpoint (sprint
    5 piece D).
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if not isinstance(decoded, dict):
            return {}
        return decoded
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def parse_cursor_datetime(raw: Any) -> datetime | None:
    """Parse a datetime that came back from ``decode_cursor``.

    Returns None if the value is missing or unparseable.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        # ``fromisoformat`` accepts the format ``encode_cursor`` writes.
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Workflow status mapping
# ---------------------------------------------------------------------------


# Open states map to "pending" on the wire. Decided states use the existing
# ``response_data`` to figure out approved vs rejected. Cancelled and
# expired pass through as-is.
_PENDING_DB_STATES = {"pending", "in_progress", "requires_approval"}
_DECIDED_DB_STATES = {"completed", "failed"}


def map_workflow_status(step: WorkflowStep) -> str:
    """Map the raw WorkflowStep.status to the API-level workflow status.

    The DB has more states than the wire surfaces (the wire mostly cares
    about the final approve/reject outcome). Decided states route through
    ``response_data["decision"]`` so a step that was "completed" by an
    approve event surfaces as ``approved``.
    """
    raw = (step.status or "").lower()
    if raw in _PENDING_DB_STATES:
        return "pending"
    if raw == "cancelled":
        return "cancelled"
    if raw == "expired":
        return "expired"
    if raw in _DECIDED_DB_STATES:
        decision = ((step.response_data or {}).get("decision") or "").lower()
        if decision == "approve":
            return "approved"
        if decision == "reject":
            return "rejected"
        # Completed without an explicit decision (legacy completion) — mirror
        # success/failure to approve/reject.
        return "approved" if raw == "completed" else "rejected"
    # Anything else: pass through. The wire schema rejects unknowns at
    # serialization, which is the desired noisy-on-drift behavior.
    return raw


def is_workflow_decided(step: WorkflowStep) -> bool:
    """True if the step is no longer pending (already approve/reject/cancel/expire)."""
    return map_workflow_status(step) != "pending"


def is_workflow_expired(step: WorkflowStep, now: datetime | None = None) -> bool:
    """True if the step has an ``expires_at`` in the past.

    ``expires_at`` is forward-compatible — the WorkflowStep model doesn't
    have a dedicated column today, so we read it from
    ``request_data["expires_at"]`` if present.
    """
    expires = (step.request_data or {}).get("expires_at")
    if not expires:
        return False
    if isinstance(expires, str):
        try:
            expires_dt = datetime.fromisoformat(expires)
        except ValueError:
            return False
    elif isinstance(expires, datetime):
        expires_dt = expires
    else:
        return False
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return current >= expires_dt


# ---------------------------------------------------------------------------
# Workflow projection
# ---------------------------------------------------------------------------


def workflow_type(step: WorkflowStep) -> str:
    """Surface ``tool_name`` first, falling back to ``step_type``."""
    return step.tool_name or step.step_type or "unknown"


def workflow_subject(step: WorkflowStep) -> tuple[str, str]:
    """Return ``(subject_type, subject_id)`` for the step.

    Pulled from the most recent ObjectWorkflowMapping — that's the canonical
    link between a workflow step and the business object it gates. If no
    mapping exists, returns sentinel ``("unknown", step.step_id)`` so the
    response still validates rather than 500-ing.
    """
    mappings = list(step.object_mappings or [])
    if not mappings:
        return "unknown", step.step_id
    # Prefer the most recently-created mapping — matches the
    # WorkflowRepository.get_latest_mapping_for_object semantics.
    latest = max(mappings, key=lambda m: m.created_at or datetime.min.replace(tzinfo=UTC))
    return latest.object_type, latest.object_id


def workflow_to_summary(
    step: WorkflowStep,
    requested_by_principal_id: str | None,
    requested_by_principal_name: str | None,
) -> WorkflowSummary:
    """Project a WorkflowStep ORM row to the API summary shape."""
    subject_type, subject_id = workflow_subject(step)
    expires = (step.request_data or {}).get("expires_at")
    expires_dt: datetime | None = None
    if isinstance(expires, str):
        try:
            expires_dt = datetime.fromisoformat(expires)
        except ValueError:
            expires_dt = None
    elif isinstance(expires, datetime):
        expires_dt = expires
    return WorkflowSummary(
        workflow_id=step.step_id,
        workflow_type=workflow_type(step),
        status=map_workflow_status(step),
        subject_type=subject_type,
        subject_id=subject_id,
        created_at=step.created_at,
        # WorkflowStep has no updated_at; the most recent change is either
        # completed_at (decided) or created_at (still open).
        updated_at=step.completed_at or step.created_at,
        expires_at=expires_dt,
        requested_by_principal_id=requested_by_principal_id,
        requested_by_principal_name=requested_by_principal_name,
    )


def workflow_to_detail(
    step: WorkflowStep,
    requested_by_principal_id: str | None,
    requested_by_principal_name: str | None,
) -> WorkflowDetail:
    """Project a WorkflowStep ORM row to the full detail shape, including
    the decisions audit trail recorded in response_data."""
    summary = workflow_to_summary(step, requested_by_principal_id, requested_by_principal_name)
    decisions_raw = (step.response_data or {}).get("decisions") or []
    decisions = [_decision_from_dict(d) for d in decisions_raw if isinstance(d, dict)]
    description = (step.request_data or {}).get("description") or ""
    return WorkflowDetail(
        **summary.model_dump(),
        description=description,
        context=dict(step.request_data or {}),
        decisions=decisions,
    )


def _decision_from_dict(payload: dict) -> WorkflowDecision:
    """Build a WorkflowDecision from a stored dict; tolerant of legacy shapes."""
    decided_at_raw = payload.get("decided_at")
    if isinstance(decided_at_raw, str):
        try:
            decided_at = datetime.fromisoformat(decided_at_raw)
        except ValueError:
            decided_at = datetime.now(UTC)
    elif isinstance(decided_at_raw, datetime):
        decided_at = decided_at_raw
    else:
        decided_at = datetime.now(UTC)
    decision = (payload.get("decision") or "approve").lower()
    if decision not in ("approve", "reject"):
        decision = "approve"
    return WorkflowDecision(
        decided_at=decided_at,
        decision=decision,
        decided_by_email=payload.get("decided_by_email"),
        decided_by_source=payload.get("decided_by_source") or "management_api",
        notes=payload.get("notes"),
    )


# ---------------------------------------------------------------------------
# Workflow decision recording
# ---------------------------------------------------------------------------


def record_workflow_decision(
    step: WorkflowStep,
    *,
    decision: str,
    notes: str | None,
    decided_by_email: str | None,
    decided_by_source: str,
    now: datetime | None = None,
) -> WorkflowDecision:
    """Mutate the WorkflowStep to record an approve/reject decision.

    Updates:
    - ``status`` flips to ``completed`` (approve) or ``failed`` (reject)
    - ``completed_at`` set
    - ``response_data`` gets ``decision``, ``decided_at``, plus an entry
      appended to ``response_data["decisions"]``

    Returns the WorkflowDecision schema instance for response shaping.

    Caller is responsible for committing the session.
    """
    decided_at = now or datetime.now(UTC)
    response: dict[str, Any] = dict(step.response_data or {})
    decisions = list(response.get("decisions") or [])

    new_decision_payload = {
        "decided_at": decided_at.isoformat(),
        "decision": decision,
        "decided_by_email": decided_by_email,
        "decided_by_source": decided_by_source,
        "notes": notes,
    }
    decisions.append(new_decision_payload)

    response["decision"] = decision
    response["decided_at"] = decided_at.isoformat()
    response["decided_by_email"] = decided_by_email
    response["decided_by_source"] = decided_by_source
    response["notes"] = notes
    response["decisions"] = decisions

    step.response_data = response
    step.status = "completed" if decision == "approve" else "failed"
    step.completed_at = decided_at

    return WorkflowDecision(
        decided_at=decided_at,
        decision=decision,
        decided_by_email=decided_by_email,
        decided_by_source=decided_by_source,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Media buy projection
# ---------------------------------------------------------------------------


def media_buy_to_summary(buy: MediaBuy, principal_name: str) -> MediaBuySummary:
    """Project a MediaBuy ORM row to the API summary shape with computed pacing."""
    raw = buy.raw_request or {}
    # buyer_ref is the caller-supplied reference, present at the top level
    # of the AdCP CreateMediaBuyRequest payload (preserved verbatim in
    # raw_request). Fall back to None when the caller didn't pass one.
    buyer_ref = raw.get("buyer_ref") if isinstance(raw, dict) else None

    pacing = compute_pacing(buy)
    return MediaBuySummary(
        media_buy_id=buy.media_buy_id,
        buyer_ref=buyer_ref,
        principal_id=buy.principal_id,
        principal_name=principal_name,
        status=buy.status,
        flight_start_date=buy.start_date,
        flight_end_date=buy.end_date,
        total_budget=buy.budget if buy.budget is not None else Decimal("0"),
        currency=buy.currency or "USD",
        delivered_impressions=None,
        delivered_spend=None,
        pacing=pacing,
        created_at=buy.created_at,
    )


def media_buy_to_detail(buy: MediaBuy, principal_name: str) -> MediaBuyDetail:
    """Project a MediaBuy ORM row to the API detail shape."""
    summary = media_buy_to_summary(buy, principal_name)
    raw = buy.raw_request or {}
    # AdCP payload structure: top-level ``packages`` list, each with
    # ``product_id``. Pull the unique product IDs in the order they appear.
    products: list[str] = []
    seen_products: set[str] = set()
    for pkg in raw.get("packages", []) if isinstance(raw, dict) else []:
        if not isinstance(pkg, dict):
            continue
        pid = pkg.get("product_id")
        if pid and pid not in seen_products:
            seen_products.add(pid)
            products.append(pid)

    targeting = raw.get("targeting") if isinstance(raw, dict) else None
    creatives_raw = raw.get("creatives") if isinstance(raw, dict) else None
    creatives = [c.get("creative_id") for c in creatives_raw or [] if isinstance(c, dict) and c.get("creative_id")]

    status_history: list[StatusEvent] = []
    if buy.created_at:
        status_history.append(StatusEvent(occurred_at=buy.created_at, status="created"))
    if buy.approved_at:
        status_history.append(
            StatusEvent(
                occurred_at=buy.approved_at,
                status="approved",
                note=f"by {buy.approved_by}" if buy.approved_by else None,
            )
        )

    return MediaBuyDetail(
        **summary.model_dump(),
        products=products,
        targeting=targeting if isinstance(targeting, dict) else None,
        creatives=creatives,
        status_history=status_history,
    )


def compute_pacing(buy: MediaBuy) -> str | None:
    """Compute pacing label from delivered vs. expected-by-now spend.

    Returns:
        ``on_pace`` if delivered ÷ expected ∈ [0.9, 1.1]
        ``underpacing`` if < 0.9
        ``overpacing`` if > 1.1
        ``None`` if the buy hasn't started, has no flight duration, or has
        no delivered metrics to compute against.

    Today the MediaBuy table doesn't carry delivered metrics directly —
    they live in the adapter's reporting feed. Until the delivery
    aggregation lands (sprint 1.5 Open Q #3), we return None for active
    buys and let the host UI render "no data yet". Buys that haven't
    started always return None per the spec.
    """
    today = datetime.now(UTC).date()
    start_date = cast(date | None, buy.start_date)
    if start_date is None or start_date > today:
        return None
    # Stub: when delivered metrics land, replace with the real ratio.
    return None


# ---------------------------------------------------------------------------
# Audit log projection
# ---------------------------------------------------------------------------


def audit_to_entry(row: AuditLog) -> AuditLogEntry:
    """Project an AuditLog ORM row to the API shape.

    Subject metadata lives in ``details`` JSON (see
    :class:`AuditLogRepository`); fall back to a sentinel if a row predates
    the convention.
    """
    details = dict(row.details or {})
    subject_type = details.pop("subject_type", None) or "unknown"
    subject_id = details.pop("subject_id", None) or ""
    actor_type = details.pop("actor_type", None) or _infer_actor_type(row)

    return AuditLogEntry(
        audit_log_id=str(row.log_id),
        occurred_at=row.timestamp,
        action=row.operation,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type=actor_type,
        actor_email=None,  # AuditLog has no local-user email column today
        external_user_email=row.external_user_email,
        external_user_id=row.external_user_id,
        external_org_id=row.external_org_id,
        external_source=row.external_source,
        details=details,
    )


def _infer_actor_type(row: AuditLog) -> str:
    """Best-effort actor-type inference for legacy rows that don't carry
    ``details["actor_type"]``."""
    if row.external_user_email:
        return "user"
    if row.principal_id:
        return "buyer_agent"
    return "system"


# ---------------------------------------------------------------------------
# Sync run projection
# ---------------------------------------------------------------------------


def sync_to_run_info(row: SyncJob) -> SyncRunInfo:
    """Project a SyncJob ORM row to the API shape."""
    duration: int | None = None
    if row.completed_at and row.started_at:
        delta = row.completed_at - row.started_at
        duration = int(delta.total_seconds())

    progress = row.progress if isinstance(row.progress, dict) else {}
    raw_counts = progress.get("counts")
    counts = raw_counts if isinstance(raw_counts, dict) else {}
    items_processed = int(
        progress.get("item_count")
        or progress.get("items_processed")
        or counts.get("products_updated")
        or counts.get("signals_updated")
        or 0
    )
    items_failed = int(progress.get("items_failed") or counts.get("keys_failed") or 0)

    # Map DB statuses to wire-side enum values. The DB uses ``running``,
    # ``pending``, ``completed``, ``failed``, ``cancelled``; the wire uses
    # ``in_progress`` for the running/pending case.
    db_status = (row.status or "").lower()
    if db_status in ("running", "pending"):
        wire_status = "in_progress"
    elif db_status == "completed":
        wire_status = "success"
    else:
        wire_status = db_status

    return SyncRunInfo(
        sync_id=row.sync_id,
        sync_type=row.sync_type,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=wire_status,
        duration_seconds=duration,
        items_processed=items_processed,
        items_failed=items_failed,
        error_summary=row.error_message,
    )
