"""Emit ``sync.completed`` and ``sync.failed`` webhooks on SyncJob terminal transitions.

Issue #463: the storefront UI proxied by agentic-api wants push notifications
when a tenant's inventory / custom_targeting / advertisers sync finishes —
without polling, and without depending on a managed-tenant scheduler keeping
state in sync. ``WEBHOOK_EVENT_TYPES`` has long declared ``sync.completed`` /
``sync.failed`` as valid subscription events, but no emission point was wired
to the sync workers.

Sync runs reach a terminal state from 15+ call sites (background workers,
adapter sync managers, admin endpoints, repository helpers). Sprinkling
``emit_event(...)`` at each site is the brittle pattern PR #457 explicitly
avoided. Instead, this module registers a SQLAlchemy session listener that
fires once per actual commit of a ``SyncJob`` row transitioning to
``completed`` or ``failed``. Same template as
``src.services.webhook_signing``'s credential-cache invalidator.

Layering:

* ``before_flush`` — snapshot the SyncJob fields needed for the payload
  while the ORM instance is still attached and its attribute history is
  available. Stash snapshots on ``session.info``.
* ``after_commit`` — drain the stash and call
  :func:`src.admin.services.webhook_publisher.emit_event` for each.
  Webhook delivery is observability, so failures here MUST NOT propagate
  back into the sync worker that just succeeded.
* ``after_rollback`` — drop the stash. A rolled-back terminal write
  should not emit an event.

The listener is idempotent and registered at module import.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_PENDING_KEY = "_sync_webhook_emission_pending"

_LISTENER_REGISTERED = False


def register_sync_webhook_emission() -> None:
    """Wire SQLAlchemy session events that emit on SyncJob terminal commits.

    Idempotent — guards against duplicate registration when the module is
    reloaded under pytest's import-fixup or during dev reloads.
    """
    global _LISTENER_REGISTERED
    if _LISTENER_REGISTERED:
        return

    # Local import: this module is in the admin layer; importing SyncJob
    # at module scope at the top would tangle the import graph during
    # tests that mock the model.
    from src.core.database.models import SyncJob

    def _capture(session: Session, *_args: Any) -> None:
        """Detect SyncJob terminal transitions and snapshot payload data.

        Runs inside ``before_flush`` so attribute history is still
        readable. Two cases:

        * ``session.dirty`` — UPDATEs. Emit only when the status attribute
          actually transitioned to a terminal value in this txn. Without
          the history check we'd re-fire on every save of a row already
          in terminal state (e.g. backfilling a column on a completed row).
        * ``session.new`` — INSERTs. Rare for terminal rows
          (``mark_pending_as_failed`` only operates on existing pending
          rows), but emit if it happens for completeness.
        """
        pending: list[dict[str, Any]] = session.info.setdefault(_PENDING_KEY, [])

        for obj in session.dirty:
            if not isinstance(obj, SyncJob):
                continue
            new_status = obj.status
            if new_status not in _TERMINAL_STATUSES:
                continue
            status_history = inspect(obj).attrs.status.history
            # ``history.added`` is non-empty only when the value changed.
            # An unchanged status (row touched for a different column)
            # leaves ``added`` empty — skip to avoid duplicate emission.
            if not status_history.added:
                continue
            pending.append(_snapshot(obj))

        for obj in session.new:
            if not isinstance(obj, SyncJob):
                continue
            if obj.status not in _TERMINAL_STATUSES:
                continue
            pending.append(_snapshot(obj))

    def _flush(session: Session) -> None:
        """Drain the snapshot stash after commit and emit events.

        Local imports keep the layering clean: this module knows about
        the publisher, not the other way round.

        The committing session (``session`` arg here) is thread-scoped
        and mid-commit at this point — it cannot accept a new
        transaction. So we open a *separate* short-lived session, bound
        to the engine directly, just for the subscriber lookup and hand
        it through ``emit_event(..., session=...)``. Dispatch
        (signing + HTTP POST) doesn't touch DB at all.
        """
        snapshots: list[dict[str, Any]] | None = session.info.pop(_PENDING_KEY, None)
        if not snapshots:
            return

        from sqlalchemy.orm import Session as RawSession

        from src.admin.services.webhook_publisher import emit_event
        from src.core.database.database_session import get_engine

        engine = get_engine()
        for snap in snapshots:
            event_type = "sync.completed" if snap["_status"] == "completed" else "sync.failed"
            try:
                with RawSession(engine) as fresh:
                    emit_event(
                        snap["tenant_id"],
                        event_type,
                        _build_payload(snap, event_type),
                        session=fresh,
                    )
            except Exception:  # pragma: no cover - emit_event already swallows
                logger.warning(
                    "sync webhook emission failed for sync_run_id=%s",
                    snap.get("sync_run_id"),
                    exc_info=True,
                )

    def _drop(session: Session) -> None:
        session.info.pop(_PENDING_KEY, None)

    event.listen(Session, "before_flush", _capture)
    event.listen(Session, "after_commit", _flush)
    event.listen(Session, "after_rollback", _drop)
    _LISTENER_REGISTERED = True


def _snapshot(job: Any) -> dict[str, Any]:
    """Capture every field needed for the payload while the row is attached.

    Done in ``before_flush`` so a subsequent attribute expiration (after
    commit, SQLAlchemy expires by default) can't make us re-read stale
    or missing data. Bare ``dict`` instead of a dataclass — this only
    travels across two callbacks in the same session.
    """
    progress = job.progress or {}
    return {
        "_status": job.status,
        "tenant_id": job.tenant_id,
        "sync_run_id": job.sync_id,
        "sync_type": job.sync_type,
        "adapter_type": job.adapter_type,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "summary": job.summary,
        "error_message": job.error_message,
        "triggered_by": job.triggered_by,
        "triggered_by_id": job.triggered_by_id,
        "item_count": progress.get("item_count") if isinstance(progress, dict) else None,
    }


def _normalize_trigger(triggered_by: str | None, triggered_by_id: str | None) -> str:
    """Map internal ``triggered_by`` taxonomy onto the public trigger Literal.

    The internal taxonomy has grown organically (``admin_ui``,
    ``admin_button``, ``scheduler_reporting``, ``order_creation``, ``api``,
    ``worker`` ...). The public surface stays at three values so integrators
    don't have to track every internal label as it shifts.

    * ``initial`` — the first-sync side effect of provisioning. Distinguished
      by ``triggered_by_id`` containing ``:provision`` (set by the
      tenant-management provision flow).
    * ``scheduled`` — recurring scheduler runs. ``triggered_by`` starting
      with ``scheduler`` (covers ``scheduler``, ``scheduler_reporting``) or
      equal to ``cron``.
    * ``manual`` — everything else (admin UI buttons, ``/refresh`` API,
      order-creation triggered cache rebuilds, worker spawns).
    """
    if triggered_by_id and ":provision" in triggered_by_id:
        return "initial"
    tb = (triggered_by or "").lower()
    if tb.startswith("scheduler") or tb == "cron":
        return "scheduled"
    return "manual"


def _include_traceback() -> bool:
    """Whether to include a ``traceback`` field in failure payloads.

    Off by default — production publishers do not want internal stack
    frames leaking through the webhook surface. ``ENVIRONMENT=development``
    or ``WEBHOOK_INCLUDE_SYNC_TRACEBACK=true`` opt in. Today the SyncJob
    row doesn't store a structured traceback (only the rendered
    ``error_message``), so this flag controls whether the rendered string
    — which may already contain a traceback when produced by
    ``_create_and_spawn_refresh`` — is forwarded.
    """
    if os.getenv("WEBHOOK_INCLUDE_SYNC_TRACEBACK", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("ENVIRONMENT", "").lower() == "development":
        return True
    return False


def _iso(value: Any) -> str | None:
    """Render a ``datetime`` as ISO-8601 with timezone, or ``None``."""
    if value is None:
        return None
    return value.isoformat()


def _build_payload(snap: dict[str, Any], event_type: str) -> dict[str, Any]:
    """Construct the ``data`` block for a sync.completed / sync.failed envelope.

    The envelope itself (``event_id``, ``event_type``, ``tenant_id``,
    ``occurred_at``, ``delivery_attempt``) is added downstream by
    :func:`src.admin.services.webhook_delivery.build_envelope`. This
    function returns only the inner ``data`` dict.
    """
    payload: dict[str, Any] = {
        "sync_run_id": snap["sync_run_id"],
        "sync_type": snap["sync_type"],
        "adapter_type": snap["adapter_type"],
        "trigger": _normalize_trigger(snap.get("triggered_by"), snap.get("triggered_by_id")),
        "started_at": _iso(snap.get("started_at")),
        "completed_at": _iso(snap.get("completed_at")),
    }

    if event_type == "sync.completed":
        payload["item_count"] = snap.get("item_count")
        payload["summary"] = snap.get("summary")
        return payload

    # sync.failed — no structured exception class on the row today, so the
    # ``error`` block is intentionally lean. ``class`` and ``category`` are
    # forward-compatible fields that will land when failure sites are
    # refactored to capture exc_info at the source. Receivers should treat
    # missing fields as absent, not assume any particular fallback.
    error_block: dict[str, Any] = {
        "message": snap.get("error_message"),
    }
    if _include_traceback() and snap.get("error_message"):
        error_block["traceback"] = snap["error_message"]
    payload["error"] = error_block
    return payload


# Wire the listener at import. Idempotent — see register_sync_webhook_emission.
register_sync_webhook_emission()
