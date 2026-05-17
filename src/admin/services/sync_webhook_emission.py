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
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_PENDING_KEY = "_sync_webhook_emission_pending"

_LISTENER_REGISTERED = False

# Max length of the public-facing ``error.message`` field. The raw
# ``SyncJob.error_message`` can carry stack frames + adapter internals;
# webhook subscribers (storefront UIs, third-party ingestion endpoints)
# don't need that. The operator-visible full string stays on the row for
# admin debugging.
_MAX_PUBLIC_ERROR_LEN = 200


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
        transaction. So we open *one* short-lived session bound to the
        engine directly, just for the subscriber lookups, and reuse it
        across every snapshot in this batch. N terminal commits in one
        txn would otherwise mean N DB round-trips and N pool checkouts
        — the security review flagged this as wasteful under bulk
        failure paths like ``mark_pending_as_failed``. Dispatch (signing
        + HTTP POST) doesn't touch DB at all.

        Snapshots are deduplicated by ``(tenant_id, sync_run_id,
        _status)`` before emission. ``before_flush`` can fire multiple
        times within a single transaction (manual ``session.flush()``
        loops, large transactions). If a row is dirty during two
        flushes with status history still showing the transition, we'd
        otherwise snapshot — and then emit — twice for the same event.
        """
        snapshots: list[dict[str, Any]] | None = session.info.pop(_PENDING_KEY, None)
        if not snapshots:
            return

        snapshots = _dedup_snapshots(snapshots)

        from sqlalchemy.orm import Session as RawSession

        from src.admin.services.webhook_publisher import emit_event
        from src.core.database.database_session import get_engine

        engine = get_engine()
        with RawSession(engine) as fresh:
            for snap in snapshots:
                event_type = "sync.completed" if snap["_status"] == "completed" else "sync.failed"
                try:
                    emit_event(
                        snap["tenant_id"],
                        event_type,
                        _build_payload(snap, event_type),
                        session=fresh,
                    )
                except Exception:  # pragma: no cover - emit_event already swallows
                    logger.warning(
                        "sync webhook emission failed for tenant_id=%s sync_run_id=%s",
                        snap.get("tenant_id"),
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


def _dedup_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate snapshots produced by multiple ``before_flush``
    invocations on the same transaction.

    Key is ``(tenant_id, sync_run_id, _status)``. First occurrence wins —
    later flushes can only carry equivalent or newer data for the same
    terminal transition, and we want at-most-one event per committed row.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for snap in snapshots:
        key = (snap["tenant_id"], snap["sync_run_id"], snap["_status"])
        if key in seen:
            continue
        seen.add(key)
        out.append(snap)
    return out


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


def _iso(value: Any) -> str | None:
    """Render a ``datetime`` as ISO-8601 with timezone, or ``None``."""
    if value is None:
        return None
    return value.isoformat()


def _public_error_message(raw: str | None) -> str | None:
    """Scrub a stored ``error_message`` for inclusion in the webhook payload.

    ``SyncJob.error_message`` is operator-facing: the spawn-failure path
    at ``tenant_management_api.py`` packs the exception class plus a
    multi-frame traceback into the field unconditionally, and adapter-side
    errors can carry GAM SOAP fault detail with internal advertiser IDs
    or OAuth refresh-token response bodies. The webhook subscriber may
    be a Slack channel, a generic ingestion endpoint, or anywhere else
    a tenant configures — none of those need stack frames.

    Strategy: first line of the rendered string, capped at
    :data:`_MAX_PUBLIC_ERROR_LEN`. The full text stays on the DB row for
    admin debugging.
    """
    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    return first_line[:_MAX_PUBLIC_ERROR_LEN]


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

    # sync.failed — ``error.message`` is scrubbed (first line, length-capped)
    # so internal stack frames and adapter response payloads don't leak
    # through the webhook surface. ``class`` and ``category`` are
    # forward-compatible fields filled in a subsequent contract pass when
    # structured exception capture lands at the failure sites.
    payload["error"] = {
        "message": _public_error_message(snap.get("error_message")),
    }
    return payload


# Wire the listener at import. Idempotent — see register_sync_webhook_emission.
register_sync_webhook_emission()
