"""Outbound webhook event publication.

[embedded-mode](../../../docs/design/embedded-mode.md).

The single entry point :func:`publish_event` is called by business code
when a tenant lifecycle event fires (workflow approved/rejected, sync
completes, etc.). Lookup of active subscriptions happens synchronously;
delivery is fire-and-forget via ``asyncio.create_task`` so the calling
request handler isn't blocked.

The plaintext webhook secret needed for signing is **not** stored in the
database — only its sha256 hash is. Therefore the publisher needs the
plaintext to be passed in by the caller that created the subscription, OR
the system must look it up via a sidecar mechanism. Sprint 6 solves this
by holding the plaintext in an in-memory cache populated at create time;
on process restart, subscriptions whose secrets aren't cached are
suspended until a cache rebuild path lands. This is a deliberate v1
limitation documented in the spec's "Open Questions" — production-grade
durable secret storage is sprint-7-or-later.

For tests and the synchronous test endpoint, the secret is supplied at the
call site (the test endpoint regenerates a fresh secret and posts with it).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import httpx

from src.admin.services.webhook_delivery import (
    _publish_one,
    _subscription_snapshot,
    build_envelope,
)
from src.core.database.database_session import get_db_session
from src.core.database.repositories import WebhookSubscriptionRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plaintext-secret cache
# ---------------------------------------------------------------------------


class _SecretCache:
    """In-memory ``webhook_id -> plaintext_secret`` cache.

    Populated when a subscription is created or its secret is rotated.
    Survives only as long as the process. A subscription whose secret
    isn't in the cache silently falls out of dispatch — receivers need
    to re-register on cold start until durable secret storage lands.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._secrets: dict[str, str] = {}

    def store(self, webhook_id: str, secret: str) -> None:
        with self._lock:
            self._secrets[webhook_id] = secret

    def get(self, webhook_id: str) -> str | None:
        with self._lock:
            return self._secrets.get(webhook_id)

    def forget(self, webhook_id: str) -> None:
        with self._lock:
            self._secrets.pop(webhook_id, None)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()


_SECRET_CACHE = _SecretCache()


def remember_webhook_secret(webhook_id: str, secret: str) -> None:
    """Cache the plaintext secret for a webhook.

    Called by the create endpoint right after the subscription is committed.
    The plaintext is needed to sign outbound deliveries; the DB stores only
    the hash.
    """
    _SECRET_CACHE.store(webhook_id, secret)


def forget_webhook_secret(webhook_id: str) -> None:
    """Drop the cached plaintext for a webhook.

    Called when a webhook is deleted/deactivated so the in-memory record
    doesn't outlive the subscription.
    """
    _SECRET_CACHE.forget(webhook_id)


def get_webhook_secret(webhook_id: str) -> str | None:
    """Return the plaintext secret for a webhook, or None if not cached."""
    return _SECRET_CACHE.get(webhook_id)


def reset_secret_cache() -> None:
    """Test helper: forget every cached secret. Production callers do not
    need this."""
    _SECRET_CACHE.clear()


# ---------------------------------------------------------------------------
# Publication entry point
# ---------------------------------------------------------------------------


# Tracks tasks created by ``publish_event`` so tests can await them. In
# production these are fire-and-forget and the runtime owns lifecycle.
_OUTSTANDING_TASKS: set[asyncio.Task] = set()


def get_outstanding_tasks() -> set[asyncio.Task]:
    """Test helper: snapshot of in-flight delivery tasks."""
    return set(_OUTSTANDING_TASKS)


def _on_task_done(task: asyncio.Task) -> None:
    _OUTSTANDING_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("webhook delivery task raised %s: %s", type(exc).__name__, exc)


def publish_event(
    tenant_id: str,
    event_type: str,
    data: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    session: Any | None = None,
) -> list[str]:
    """Fire a tenant lifecycle event to all active subscribers.

    Synchronous lookup; asynchronous fire-and-forget delivery. Returns the
    list of ``webhook_id`` values that were dispatched (empty if no
    subscriber matched).

    The caller is a Flask request handler running outside an asyncio loop;
    we manage the loop locally for the duration of the dispatch enqueue.

    For the four "in-flight" events (workflow.* / media_buy.* / sync.* /
    tenant.config_changed), :func:`publish_event` is the only correct
    entry point — never POST directly to a subscription URL from elsewhere.

    :param session: Optional caller-supplied SQLAlchemy session for the
        subscriber lookup. Used by callers that fire from inside a
        SQLAlchemy event listener (e.g.
        :mod:`src.admin.services.sync_webhook_emission`) where the
        thread-scoped session is mid-commit and cannot accept a new
        transaction. The caller owns the session's lifecycle; this
        function does not close or commit it. When ``None`` (the normal
        Flask request-handler path), a fresh session is opened via
        :func:`get_db_session`.
    """
    if session is not None:
        repo = WebhookSubscriptionRepository(session, tenant_id)
        subscribers = repo.list_for_event(event_type)
        snapshots = [_subscription_snapshot(s) for s in subscribers]
    else:
        with get_db_session() as fresh_session:
            repo = WebhookSubscriptionRepository(fresh_session, tenant_id)
            subscribers = repo.list_for_event(event_type)
            snapshots = [_subscription_snapshot(s) for s in subscribers]

    if not snapshots:
        return []

    delivered_ids: list[str] = []
    for snap in snapshots:
        secret = get_webhook_secret(snap["webhook_id"])
        if secret is None:
            logger.warning(
                "webhook %s has no cached secret — skipping (re-register to restore)",
                snap["webhook_id"],
            )
            continue
        envelope = build_envelope(event_type=event_type, tenant_id=tenant_id, data=data)
        _schedule_delivery(snap, envelope, secret, client=client)
        delivered_ids.append(snap["webhook_id"])

    return delivered_ids


def emit_event(
    tenant_id: str,
    event_type: str,
    data: dict[str, Any],
    *,
    session: Any | None = None,
) -> None:
    """Best-effort wrapper around :func:`publish_event`.

    Webhook delivery is observability, not a critical-path commit. A
    delivery (or even subscriber-lookup) failure must never propagate
    back to the caller and roll back the operation that fired the event.

    Every tenant-lifecycle emission point should call ``emit_event``
    instead of writing its own try/except around :func:`publish_event` —
    centralizing the swallow keeps the contract uniform and lets us
    instrument the failure path in one place if/when delivery moves to
    a real queue.

    See :func:`publish_event` for the ``session`` parameter.
    """
    try:
        publish_event(tenant_id, event_type, data, session=session)
    except Exception:  # pragma: no cover — defensive; publish_event catches its own errors
        logger.warning("publish_event(%s) failed", event_type, exc_info=True)


def _schedule_delivery(
    snapshot: dict[str, Any],
    envelope: dict[str, Any],
    secret: str,
    *,
    client: httpx.AsyncClient | None,
) -> None:
    """Schedule the delivery coroutine on the running loop, or run it inline.

    Flask request handlers don't run inside asyncio. We try to reuse a
    running loop if one exists (tests with an event loop running); otherwise
    we run the dispatch in a fresh loop for the duration of the call. The
    fresh-loop path is synchronous from the caller's perspective; that
    matches "fire and forget" since we don't await the delivery, just the
    enqueue.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(_publish_one(snapshot, envelope, secret, client=client))
        _OUTSTANDING_TASKS.add(task)
        task.add_done_callback(_on_task_done)
        return

    # No running loop — Flask handlers are sync. Dispatch via a short-lived
    # loop. The HTTP POST runs to completion; from the caller's perspective
    # it's fire-and-forget *of subsequent retries* (which a follow-up
    # supervisor will own), not of the first delivery itself.
    asyncio.run(_publish_one(snapshot, envelope, secret, client=client))
