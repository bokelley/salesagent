"""Webhook delivery service for Sprint 6 outbound webhooks.

Lean wrapper around the AdCP SDK's signing primitives. Sign-and-POST is
delegated to :func:`adcp.webhooks.sign_legacy_webhook` so the on-wire
HMAC-SHA256 format matches what the SDK's verifier expects — the wire shape
mirrors AdCP-legacy webhook auth (``X-AdCP-Signature: sha256=<hex>`` over
``f"{timestamp}.{body}"``, with ``X-AdCP-Timestamp`` carrying the unix
seconds). Buyers verify with :func:`adcp.signing.webhook_hmac.verify_webhook_hmac`.

[embedded-mode](../../../docs/design/embedded-mode.md).

The supervisor's ``send_mcp`` API is hardcoded to MCP-task webhook
semantics (task_id + status), so we don't use it directly for the
event payloads. The signing primitives are SDK-owned; only the dispatch
loop is local.

Two delivery paths:

1. **Synchronous test path** (:func:`deliver_event_sync`) — used by
   ``POST /webhooks/{wid}/test``. Posts in the request handler so the
   caller gets the response status synchronously.

2. **Fire-and-forget event publication** (:func:`publish_event`) — used
   when business code emits an event. Looks up active subscriptions and
   schedules deliveries via ``asyncio.create_task`` (best-effort delivery
   in the current process). Durable retry / DLQ is a follow-up; the
   subscription's ``consecutive_failures`` counter is updated synchronously
   so subsequent admin reads reflect failure state.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from adcp.webhooks import sign_legacy_webhook

from src.core.database.database_session import get_db_session
from src.core.database.models import WebhookSubscription
from src.core.database.repositories import WebhookSubscriptionRepository
from src.core.database.repositories.webhook_subscription import hash_secret  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


_PRIVATE_BLOCKLIST_DEFAULT_HINTS = (
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "127.",
    "0.",
    "169.254.",
)


def _allow_private_destinations() -> bool:
    """Whether to permit private/localhost webhook URLs.

    Defaults False (production-safe). Set ``WEBHOOK_ALLOW_PRIVATE_IPS=true``
    or ``ADCP_AUTH_TEST_MODE=true`` for dev/CI fixtures. The flag is consulted
    at call time so tests can flip it via monkeypatch.
    """
    if os.getenv("WEBHOOK_ALLOW_PRIVATE_IPS", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("ADCP_AUTH_TEST_MODE", "").lower() in ("1", "true", "yes"):
        return True
    return False


class WebhookUrlError(ValueError):
    """Raised for URL violations the API surfaces as 400 ``webhook_url_*`` errors."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_webhook_url(url: str) -> str:
    """Validate a candidate webhook URL.

    Enforces HTTPS and (when private-IP gating is on) a coarse blocklist
    of private/loopback ranges to defend against trivial SSRF.

    The SDK's :class:`adcp.signing.IpPinnedTransport` runs the deeper
    DNS-resolution-and-pinning check at delivery time; this layer is the
    fast-fail at create/test time so callers get a 400 instead of a 502.
    """
    stripped = (url or "").strip()
    if not stripped:
        raise WebhookUrlError("webhook_url_invalid", "url must be non-empty")
    if not stripped.startswith("https://"):
        # In test/dev mode allow http:// URLs for local mock receivers.
        if not (_allow_private_destinations() and stripped.startswith("http://")):
            raise WebhookUrlError(
                "webhook_url_not_https",
                f"url must start with 'https://'; got {url!r}",
            )

    if _allow_private_destinations():
        return stripped

    # Coarse blocklist on the hostname segment. Production receivers should
    # never be reachable on RFC1918 / loopback / link-local space; if a buyer
    # legitimately needs that they'll set the env flag.
    host = _extract_host(stripped)
    for hint in _PRIVATE_BLOCKLIST_DEFAULT_HINTS:
        if host.startswith(hint):
            raise WebhookUrlError(
                "webhook_url_blocked",
                f"url host {host!r} is in a private/loopback range; set "
                "WEBHOOK_ALLOW_PRIVATE_IPS=true to override (dev only)",
            )
    if host in ("localhost",):
        raise WebhookUrlError(
            "webhook_url_blocked",
            "url host 'localhost' is blocked; set WEBHOOK_ALLOW_PRIVATE_IPS=true (dev only)",
        )
    return stripped


def _extract_host(url: str) -> str:
    """Return the bare hostname (no scheme, port, or path) for blocklist matching."""
    rest = url.split("://", 1)[1] if "://" in url else url
    host = rest.split("/", 1)[0]
    host = host.split(":", 1)[0]  # strip port
    return host.lower()


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


# Envelope-level schema version. Bumps when any event's ``data`` block
# shape changes in a breaking way (added required field, removed field,
# renamed key, changed type). Receivers can use this to gate consumption
# of the wire format. Today only one version exists; the field is here
# so future bumps don't require parallel webhook URLs to roll out.
EVENT_SCHEMA_VERSION = "1"


def build_envelope(
    *,
    event_type: str,
    tenant_id: str,
    data: dict[str, Any],
    delivery_attempt: int = 1,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical Sprint 6 wire envelope.

    Shape per the spec's "Payload format" section. The same ``event_id`` is
    reused on retries so receivers can dedupe; ``delivery_attempt`` increments
    on each retry. ``event_schema_version`` is at the envelope level so any
    event-type's data block can rev independently when it ships a breaking
    change.
    """
    return {
        "event_id": event_id or f"evt_{uuid.uuid4().hex}",
        "event_type": event_type,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "delivery_attempt": delivery_attempt,
        "data": data,
    }


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _allowed_extra_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Filter out reserved/unsafe headers from a subscription's static headers.

    Matches the SDK's reserved-header list: signature, content-digest,
    host-class headers must come from the signing layer, never from a
    buyer-supplied static header.
    """
    if not headers:
        return {}
    blocked = {
        "content-type",
        "content-length",
        "content-digest",
        "host",
        "authorization",
        "signature",
        "signature-input",
        "x-adcp-signature",
        "x-adcp-timestamp",
    }
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


async def _post_signed(
    url: str,
    secret: str,
    payload: dict[str, Any],
    extra_headers: dict[str, str] | None,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> tuple[int | None, int, str | None]:
    """Sign + POST one envelope. Returns ``(status_code, latency_ms, error)``.

    ``status_code`` is None on a transport error (timeout, connection
    refused) — the ``error`` string carries the diagnostic.

    Uses :func:`adcp.webhooks.sign_legacy_webhook` so the bytes signed match
    the bytes posted. The caller may pass ``client=`` to share a connection
    pool (typically the test harness passes an ASGI transport).
    """
    headers, body = sign_legacy_webhook(secret, payload)
    headers["Content-Type"] = "application/json"
    for k, v in _allowed_extra_headers(extra_headers).items():
        headers[k] = v

    t0 = time.monotonic()
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as own_client:
                response = await own_client.post(url, content=body, headers=headers)
        else:
            response = await client.post(url, content=body, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        latency = int((time.monotonic() - t0) * 1000)
        return None, latency, f"{type(exc).__name__}: {exc}"

    latency = int((time.monotonic() - t0) * 1000)
    return response.status_code, latency, None


# ---------------------------------------------------------------------------
# Subscription-aware helpers
# ---------------------------------------------------------------------------


async def deliver_event_sync(
    subscription: WebhookSubscription,
    secret: str,
    envelope: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[int | None, int, str | None]:
    """Deliver one event synchronously and update the subscription's stats.

    Returns ``(status_code, latency_ms, error)``. The subscription's
    ``last_delivery_at``, ``last_delivery_status``, and
    ``consecutive_failures`` are updated and committed in a fresh session
    so the test-endpoint caller sees fresh state on the next read.
    """
    status_code, latency_ms, error = await _post_signed(
        url=subscription.url,
        secret=secret,
        payload=envelope,
        extra_headers=subscription.extra_headers,
        client=client,
    )

    success = status_code is not None and 200 <= status_code < 300
    now = datetime.now(UTC)

    with get_db_session() as session:
        repo = WebhookSubscriptionRepository(session, subscription.tenant_id)
        fresh = repo.get_by_id(subscription.webhook_id, include_inactive=True)
        if fresh is not None:
            repo.record_delivery(fresh, status_code=status_code, success=success, now=now)
            session.commit()

    return status_code, latency_ms, error


async def _publish_one(
    sub_snapshot: dict[str, Any],
    envelope: dict[str, Any],
    secret: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Background-task body: POST the envelope to one subscription.

    ``sub_snapshot`` is a plain-dict copy of the relevant subscription
    fields — pulling a detached ORM object across sessions is brittle.
    """
    status_code, latency_ms, error = await _post_signed(
        url=sub_snapshot["url"],
        secret=secret,
        payload=envelope,
        extra_headers=sub_snapshot.get("extra_headers"),
        client=client,
    )
    success = status_code is not None and 200 <= status_code < 300
    now = datetime.now(UTC)

    try:
        with get_db_session() as session:
            repo = WebhookSubscriptionRepository(session, sub_snapshot["tenant_id"])
            fresh = repo.get_by_id(sub_snapshot["webhook_id"], include_inactive=True)
            if fresh is not None:
                repo.record_delivery(fresh, status_code=status_code, success=success, now=now)
                session.commit()
    except Exception:
        logger.warning(
            "failed to update delivery stats for webhook=%s",
            sub_snapshot.get("webhook_id"),
            exc_info=True,
        )

    if not success:
        logger.warning(
            "webhook delivery failed: webhook=%s event=%s status=%s error=%s latency=%dms",
            sub_snapshot.get("webhook_id"),
            envelope.get("event_type"),
            status_code,
            error,
            latency_ms,
        )


def _subscription_snapshot(sub: WebhookSubscription) -> dict[str, Any]:
    """Capture the dispatch-relevant fields so the dispatcher doesn't reach
    back through the ORM after the publishing session closes."""
    return {
        "webhook_id": sub.webhook_id,
        "tenant_id": sub.tenant_id,
        "url": sub.url,
        "extra_headers": dict(sub.extra_headers) if sub.extra_headers else None,
    }
