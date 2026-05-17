"""Emit ``sync_run.completed`` and ``sync_run.failed`` webhooks on SyncJob terminal transitions.

Issue #463: the storefront UI proxied by agentic-api wants push notifications
when a tenant's inventory / custom_targeting / advertisers sync finishes —
without polling, and without depending on a managed-tenant scheduler keeping
state in sync. The catalog originally declared ``sync.completed`` /
``sync.failed`` but no emission point was wired; PR #465 wired emission and
renamed the events to ``sync_run.*`` to match the ``<entity>.<verb-past>``
catalog convention (the entity is the SyncJob row, surfaced as
``data.sync_run_id``).

Architecture: ``_capture`` (before_flush) collects snapshots into a thread-safe
queue. ``_flush`` (after_commit) moves them from the SQLAlchemy session into
the queue — that's all the work it does in-line. A background dispatcher
thread polls the queue and does the actual DB lookup + delivery. This is
critical for CI safety (#76424016996): if the listener did DB work in-line
under after_commit, a daemon thread racing the integration_db fixture's
``engine.dispose()`` would raise :class:`OperationalError` through
``session.commit()``, trip the ``_is_healthy=False`` circuit breaker in
``get_db_session``, and cascade-fail every subsequent test in the 10s
breaker window. Deferring to a separate thread completely isolates emission
from the committing session.

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
import queue
import threading
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_PENDING_KEY = "_sync_webhook_emission_pending"

_LISTENER_REGISTERED = False

# Process-wide queue of snapshots awaiting emission. ``_flush`` drains
# from session.info and enqueues here; the dispatcher thread polls and
# performs the actual subscriber lookup + delivery. Keeping the queue
# unbounded is fine — emission load is bounded by the SyncJob commit
# rate, which is small in practice.
_DISPATCH_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue()
_DISPATCHER_THREAD: threading.Thread | None = None
_DISPATCHER_LOCK = threading.Lock()

# Max length of the public-facing ``error.message`` field. The raw
# ``SyncJob.error_message`` can carry stack frames + adapter internals;
# webhook subscribers (storefront UIs, third-party ingestion endpoints)
# don't need that. The operator-visible full string stays on the row for
# admin debugging.
_MAX_PUBLIC_ERROR_LEN = 200


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

    Wrapped in a top-level try/except: webhook emission is best-effort
    observability and MUST NOT raise into the committing session.
    Any exception here would propagate to the caller's
    ``session.commit()``, hit the ``OperationalError`` handler in
    :func:`get_db_session`, trip the ``_is_healthy=False`` circuit
    breaker, and cascade-fail every subsequent test (CI #76422772268).
    """
    try:
        # Local import: this module is in the admin layer; importing
        # SyncJob at module scope would tangle the import graph during
        # tests that mock the model.
        from src.core.database.models import SyncJob

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
    except Exception:  # pragma: no cover - defensive, must not raise
        logger.warning("sync webhook emission _capture failed", exc_info=True)


def _flush(session: Session) -> None:
    """Move snapshots from session.info to the process-wide dispatch queue.

    Runs in ``after_commit``. Does ZERO DB work in-line — the listener
    must not touch the engine here, because the committing session is
    mid-commit and any exception we raise propagates through
    ``session.commit()`` into :func:`get_db_session`'s ``OperationalError``
    handler, trips ``_is_healthy=False``, and cascade-fails every
    subsequent test in the 10s circuit-breaker window (CI #76424016996).

    The dispatcher thread does the actual subscriber lookup + delivery,
    fully isolated from the committing session's lifecycle. Wrapped in
    try/except as a final belt-and-suspenders — even ``queue.put`` or
    ``session.info.pop`` should not bring down a committing session.

    Snapshots are deduplicated by ``(tenant_id, sync_run_id, _status)``
    before enqueueing. ``before_flush`` can fire multiple times within
    a single transaction (manual ``session.flush()`` loops, large
    transactions). If a row is dirty during two flushes with status
    history still showing the transition, we'd otherwise enqueue — and
    then emit — twice for the same event.
    """
    try:
        snapshots: list[dict[str, Any]] | None = session.info.pop(_PENDING_KEY, None)
        if not snapshots:
            return

        for snap in _dedup_snapshots(snapshots):
            _DISPATCH_QUEUE.put_nowait(snap)

        # Lazy-start the dispatcher on first enqueue. Module-import time
        # is too early — the database engine isn't yet configured during
        # some import paths (Alembic migrations, schema introspection).
        _ensure_dispatcher_running()
    except Exception:  # pragma: no cover - defensive, must not raise
        logger.warning("sync webhook emission _flush failed", exc_info=True)


def _ensure_dispatcher_running() -> None:
    """Start the dispatcher thread if it isn't already running.

    Idempotent under thread contention via ``_DISPATCHER_LOCK``. The
    thread is a daemon so process shutdown doesn't block on draining
    the queue — the v1 best-effort contract accepts dropped events at
    shutdown.
    """
    global _DISPATCHER_THREAD
    if _DISPATCHER_THREAD is not None and _DISPATCHER_THREAD.is_alive():
        return
    with _DISPATCHER_LOCK:
        if _DISPATCHER_THREAD is not None and _DISPATCHER_THREAD.is_alive():
            return
        _DISPATCHER_THREAD = threading.Thread(
            target=_dispatcher_loop,
            name="sync-webhook-dispatcher",
            daemon=True,
        )
        _DISPATCHER_THREAD.start()


def _dispatcher_loop() -> None:
    """Drain the dispatch queue and emit events.

    Blocks on ``queue.get`` so the thread sleeps when idle. Each
    snapshot is processed in its own try/except — a single failed
    emission doesn't take down the dispatcher.
    """
    while True:
        try:
            snap = _DISPATCH_QUEUE.get()
        except Exception:  # pragma: no cover - queue should not raise
            logger.debug("sync webhook dispatcher queue.get raised", exc_info=True)
            continue
        try:
            _dispatch_one(snap)
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "sync webhook dispatcher failed for tenant_id=%s sync_run_id=%s",
                snap.get("tenant_id"),
                snap.get("sync_run_id"),
                exc_info=True,
            )
        finally:
            try:
                _DISPATCH_QUEUE.task_done()
            except Exception:
                logger.debug("sync webhook dispatcher task_done raised", exc_info=True)


def _dispatch_one(snap: dict[str, Any]) -> None:
    """Build payload and emit one event. Uses the project-wide
    ``get_db_session()`` rather than a raw ``Session(engine)`` because
    we're in our own thread now — the post-commit race is gone."""
    from src.admin.services.webhook_publisher import emit_event

    event_type = "sync_run.completed" if snap["_status"] == "completed" else "sync_run.failed"
    emit_event(
        snap["tenant_id"],
        event_type,
        _build_payload(snap, event_type),
    )


def wait_for_dispatch(timeout: float = 5.0) -> None:
    """Block until the dispatch queue is fully drained.

    Test helper — production callers don't need to know about queue
    timing. Integration tests that assert "the receiver got the event"
    must call this after the committing ``session.commit()`` so the
    daemon dispatcher has time to look up subscribers and post.

    Raises :class:`TimeoutError` if the queue isn't drained within
    ``timeout`` seconds; this surfaces a stuck dispatcher in tests
    rather than letting them hang.
    """
    # Snapshot the unfinished count; queue.join() doesn't accept a
    # timeout, so poll on the internal counter.
    import time

    deadline = time.monotonic() + timeout
    while _DISPATCH_QUEUE.unfinished_tasks > 0:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"sync webhook dispatch queue still has "
                f"{_DISPATCH_QUEUE.unfinished_tasks} unprocessed snapshots after {timeout}s"
            )
        time.sleep(0.01)


def _drop(session: Session) -> None:
    try:
        session.info.pop(_PENDING_KEY, None)
    except Exception:  # pragma: no cover - defensive, must not raise
        logger.warning("sync webhook emission _drop failed", exc_info=True)


def register_sync_webhook_emission() -> None:
    """Wire SQLAlchemy session events that emit on SyncJob terminal commits.

    Idempotent — guards against duplicate registration when the module is
    reloaded under pytest's import-fixup or during dev reloads.
    """
    global _LISTENER_REGISTERED
    if _LISTENER_REGISTERED:
        return

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


# Internal ``triggered_by`` labels that map to the public ``manual`` trigger.
# Positive-list — anything not on this list AND not matching the
# provisioning / scheduled branches surfaces as ``unknown``, which lets
# receivers detect taxonomy drift instead of silently misattributing new
# internal labels to user action.
_MANUAL_TRIGGERED_BY = frozenset(
    {
        "admin_ui",
        "admin_button",
        "admin_scheduling_ui",
        "api",
        "order_creation",
        "worker",
    }
)


def _normalize_trigger(triggered_by: str | None, triggered_by_id: str | None) -> str:
    """Map internal ``triggered_by`` taxonomy onto the public trigger Literal.

    The internal taxonomy has grown organically (``admin_ui``,
    ``admin_button``, ``scheduler_reporting``, ``order_creation``, ``api``,
    ``worker`` ...). The public surface stays at four values so integrators
    don't have to track every internal label as it shifts.

    * ``provisioning`` — first-sync side effect of provisioning. Detected
      via ``triggered_by_id`` containing ``:provision`` (set by the
      tenant-management provision flow). Named after the call-site
      signal, not the semantic "this is the first ever sync" — a
      re-provision of an existing tenant still emits ``provisioning``.
    * ``scheduled`` — recurring scheduler runs. ``triggered_by`` starting
      with ``scheduler`` (covers ``scheduler``, ``scheduler_reporting``)
      or equal to ``cron``.
    * ``manual`` — positive-match against a known set of user-driven
      labels (admin UI buttons, ``/refresh`` API, order-creation triggered
      cache rebuilds, worker spawns).
    * ``unknown`` — anything else. Default lets receivers detect drift
      when a new internal label lands without a normalizer update,
      instead of silently misattributing it to a user action.
    """
    if triggered_by_id and ":provision" in triggered_by_id:
        return "provisioning"
    tb = (triggered_by or "").lower()
    if tb.startswith("scheduler") or tb == "cron":
        return "scheduled"
    if tb in _MANUAL_TRIGGERED_BY:
        return "manual"
    return "unknown"


# Substring fingerprints used to bucket a raw ``error_message`` into the
# public ``error.category`` taxonomy. Case-insensitive substring match,
# first-bucket-wins. The classifier is intentionally crude — its job is
# to give storefront UI enough signal to pick a CTA (Retry vs Reconnect
# vs Contact admin) without substring-matching our exception strings
# themselves. When structured exception capture lands at the failure
# sites, this fallback shrinks to the small-residue case.
_ERROR_CATEGORY_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "auth",
        (
            "refresh token",
            "invalid_grant",
            "unauthoriz",
            "permission denied",
            "insufficient permissions",
            "403",
            "401",
        ),
    ),
    (
        "transient",
        (
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "rate limit",
            "throttle",
            "5xx",
            "503",
            "502",
            "500",
            "temporarily unavailable",
        ),
    ),
)


def _classify_error(message: str | None) -> str:
    """Bucket a raw ``error_message`` into a coarse public category.

    Three values: ``auth`` (operator action: reconnect GAM / rotate keys),
    ``transient`` (caller action: retry), ``permanent`` (everything else
    — operator investigation). Receivers shouldn't make load-bearing
    decisions off this beyond CTA selection.
    """
    if not message:
        return "permanent"
    lowered = message.lower()
    for category, fingerprints in _ERROR_CATEGORY_FINGERPRINTS:
        if any(fp in lowered for fp in fingerprints):
            return category
    return "permanent"


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
    """Construct the ``data`` block for a sync_run.completed / sync_run.failed envelope.

    The envelope itself (``event_id``, ``event_type``, ``event_schema_version``,
    ``tenant_id``, ``occurred_at``, ``delivery_attempt``) is added
    downstream by :func:`src.admin.services.webhook_delivery.build_envelope`.
    This function returns only the inner ``data`` dict.

    Contract rule: every known key is always emitted with at least
    ``null``. Receivers codegen TS/Python types from the OpenAPI spec
    and benefit from stable key presence — adding a value later (e.g.
    structured ``error.class`` when failure sites capture exc_info) is
    then a value change, not a schema change.
    """
    payload: dict[str, Any] = {
        "sync_run_id": snap["sync_run_id"],
        "sync_type": snap["sync_type"],
        "adapter_type": snap["adapter_type"],
        "trigger": _normalize_trigger(snap.get("triggered_by"), snap.get("triggered_by_id")),
        "started_at": _iso(snap.get("started_at")),
        "completed_at": _iso(snap.get("completed_at")),
    }

    if event_type == "sync_run.completed":
        payload["item_count"] = snap.get("item_count")
        payload["summary"] = snap.get("summary")
        return payload

    # sync_run.failed — ``error.message`` is scrubbed (first line,
    # length-capped). ``error.class`` is reserved for the future
    # structured-exception capture work and emitted as ``null`` today
    # so codegen'd TS types stay stable when it lands. ``error.category``
    # is bucketed crudely from the error_message so storefront UIs can
    # pick a CTA without substring-matching our exception strings.
    public_message = _public_error_message(snap.get("error_message"))
    payload["error"] = {
        "message": public_message,
        "class": None,
        "category": _classify_error(snap.get("error_message")),
    }
    return payload


# Wire the listener at import. Idempotent — see register_sync_webhook_emission.
register_sync_webhook_emission()
