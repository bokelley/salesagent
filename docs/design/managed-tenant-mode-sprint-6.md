# Sprint 6 Spec: Outbound Webhooks (Optional)

**Parent design:** [managed-tenant-mode.md](./managed-tenant-mode.md)
**Builds on:** [sprint 1](./managed-tenant-mode-sprint-1.md) – [sprint 5](./managed-tenant-mode-sprint-5.md)
**Status:** Draft, optional
**Last updated:** 2026-05-04

## Scope

Sprint 6 is optional. It replaces polling-based observability (sprint 1.5 status + sprint 3 workflows/audit-log) with push-based notifications, so Scope3 doesn't have to call `GET /status` and `GET /workflows` on a schedule to surface live state. Worth doing once polling load becomes problematic or once Scope3 wants near-real-time UX (e.g., a workflow approval notification appears in Storefront seconds after it's created in the salesagent).

Scope:
1. Webhook subscription management (register/list/delete URLs per tenant).
2. Event publication for ~6 event types covering operational changes.
3. Signed payload delivery with at-least-once semantics, exponential backoff, dead-letter queue.

5 endpoints + an event-publication subsystem:

```
GET     /tenants/{tid}/webhooks
POST    /tenants/{tid}/webhooks
GET     /tenants/{tid}/webhooks/{wid}
DELETE  /tenants/{tid}/webhooks/{wid}
POST    /tenants/{tid}/webhooks/{wid}/test
```

## Event types

| Event | When | Payload subject |
|---|---|---|
| `tenant.adapter_connection_lost` | Adapter test fails N consecutive times | adapter status |
| `tenant.adapter_connection_restored` | Adapter test succeeds after a lost-connection event | adapter status |
| `sync.completed` | A GAM sync finishes (any status) | sync run info |
| `workflow.created` | A workflow becomes pending | workflow detail |
| `workflow.decided` | A workflow is approved/rejected | workflow detail with decision |
| `media_buy.status_changed` | Media buy status transition (approval, delivery start, completion, failure) | media buy detail with previous status |

Sprint 6 does not aim to be exhaustive — these six cover the main operational signals. More can be added incrementally; the publication subsystem is generic.

## Webhook subscription schemas

```python
class WebhookSubscriptionCreateRequest(BaseModel):
    url: HttpUrl                        # https-only enforced
    event_types: list[str]              # subset of supported events; empty = all
    description: str | None = None      # for the human registering it
    headers: dict[str, str] | None = None  # static headers added to every request, e.g., bearer token

class WebhookSubscriptionDetail(BaseModel):
    webhook_id: str
    url: str
    event_types: list[str]
    description: str | None
    is_active: bool
    secret: SecretStr                   # for HMAC signing — returned exactly once at create
    created_at: datetime
    last_delivery_at: datetime | None
    last_delivery_status: int | None    # last HTTP response code
    consecutive_failures: int           # for backoff/disablement tracking

class ListWebhooksResponse(BaseModel):
    webhooks: list[WebhookSubscriptionDetail]  # secret omitted in list view
    count: int

class WebhookTestResponse(BaseModel):
    delivered: bool
    response_status: int | None
    response_body: str | None           # truncated to 1KB
    latency_ms: int | None
    error: str | None
```

## Payload format

Every webhook delivery is a POST with the same envelope:

```json
{
  "event_id": "evt_01HQXR...",
  "event_type": "workflow.created",
  "tenant_id": "tenant_abc",
  "occurred_at": "2026-05-04T18:23:11.443Z",
  "delivery_attempt": 1,
  "data": { /* event-type-specific payload */ }
}
```

Headers:
- `Content-Type: application/json`
- `X-Salesagent-Event: workflow.created`
- `X-Salesagent-Delivery: evt_01HQXR...`
- `X-Salesagent-Signature: sha256=<hex>` — HMAC-SHA256 of the raw body using the subscription's secret

Plus any static headers from the subscription (`headers` field).

Receivers verify the signature before processing. The `event_id` is a stable per-event ULID; redeliveries (after a transient failure) carry the same `event_id` with incremented `delivery_attempt` so receivers can dedupe.

## Delivery semantics

**At-least-once.** Receivers must be idempotent — keyed on `event_id`.

**Retry policy.** Exponential backoff: 30s, 2min, 10min, 1h, 6h. Five attempts, then move to dead-letter queue. After 10 consecutive failures across all events, the subscription auto-disables (`is_active=false`); re-enable requires PATCH or recreate.

**Ordering.** Events delivered in the order they occurred per tenant, but receivers should not assume strict ordering across event types — concurrent deliveries are possible.

**Implementation choice.** The salesagent already has Celery / RQ / a similar background-task layer (confirm at implementation time). Webhook deliveries enqueue a task on event publication; the worker handles HTTP posting + retries. Persist event records in a `webhook_events` table for the dead-letter queue and for replay.

**Synchronous test endpoint.** `POST /webhooks/{wid}/test` posts a minimal `webhook.test` payload synchronously (≤10s) and returns the response. Used by Scope3 when registering a webhook to verify the receiver works.

## Security

- **HTTPS only**: webhook URLs without `https://` rejected at create. Local-dev exception via env flag.
- **HMAC signing**: shared secret per subscription, generated at create, returned once. Lost secrets require re-registering the webhook (no retrieval endpoint).
- **No outbound calls to private IPs** by default — block 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8 unless `WEBHOOK_ALLOW_PRIVATE_IPS=true`. Prevents SSRF via webhook URL.
- **Replay attack protection**: the `occurred_at` timestamp + `event_id` give receivers everything they need; sprint 6 doesn't ship its own replay protection beyond the signature.

## Error responses

Reuses sprint 1's `ApiError`. New error codes:

| HTTP | code | When |
|---|---|---|
| 400 | `webhook_url_not_https` | URL not HTTPS |
| 400 | `webhook_url_blocked` | Private/internal IP in URL |
| 400 | `webhook_event_types_unknown` | Subscription requests an event type the salesagent doesn't publish |
| 404 | `webhook_not_found` | `{wid}` doesn't exist or wrong tenant |

## Acceptance criteria

**Subscription CRUD:**
- [ ] Create with valid HTTPS URL succeeds; returns secret exactly once.
- [ ] Create with HTTP URL returns 400.
- [ ] Create with private IP (in default config) returns 400.
- [ ] List omits `secret` field; create response includes it.
- [ ] Empty `event_types` means "subscribe to all" (validated by integration test).
- [ ] Delete cleans up pending deliveries (dead-letter records preserved).

**Delivery:**
- [ ] `workflow.created` event triggers a POST to all subscribed webhooks.
- [ ] HMAC signature verifies against the subscription secret.
- [ ] Failed delivery (5xx or timeout) retries with exponential backoff.
- [ ] After 5 attempts, event lands in dead-letter queue with original payload + attempt history.
- [ ] After 10 consecutive failures across events, subscription auto-disables.

**Test endpoint:**
- [ ] `POST /webhooks/{wid}/test` posts synchronously and returns response status + body.
- [ ] Test failure includes diagnostic info (timeout, connection refused, etc.).

**Idempotency / dedup:**
- [ ] Two retries of the same event share an `event_id` with incremented `delivery_attempt`.
- [ ] Receiver test (using a mock receiver in integration tests) confirms idempotency keyed on `event_id`.

**Integration:**
- [ ] End-to-end: register webhook on managed tenant, create a workflow via buyer protocol, confirm webhook fires within 30s with valid signature and correct payload.
- [ ] End-to-end: configure a 502-returning mock receiver, confirm retry → eventual DLQ landing.

## Open questions

1. **Background-task infrastructure.** Confirm the salesagent has a worker stack ready (Celery/RQ/etc.) or whether sprint 6 introduces one. If new, this sprint grows substantially. Investigate before committing.
2. **Per-tenant rate limits on webhook delivery.** A misbehaving tenant (e.g., a workflow that flaps approve/reject in a loop) could spam Scope3's receiver. Add per-tenant per-event-type rate limits in sprint 6, or defer.
3. **Replay endpoint.** Scope3 may want to replay past events into a new receiver (e.g., after a Storefront rewrite). Add `POST /webhooks/{wid}/replay?from=ISO_DATE` or defer.
4. **DLQ surface.** Dead-letter records are stored but no API surfaces them. Add `GET /webhooks/{wid}/dead-letter` later; not sprint 6 v1.
5. **Static-header secrets.** Subscription `headers` field can carry a bearer token. Store encrypted; never log. Consider whether this should be a separate `auth_config` field with a discriminated union (none/bearer/basic) for clarity. Defer to implementation review.

## After sprint 6

Managed mode is feature-complete. The integration covers: provisioning (sprint 1), storefront integration essentials (sprint 1.5), runtime hardening (sprint 2), workflow mutations + drill-down reads (sprint 3), publisher-managed CRUD via API (sprints 4–5, optional), async notifications (sprint 6, optional). Open instances continue working in parallel.

Future work, not part of this design line:
- Multi-control-plane support (per-control-plane API keys, scoped permissions).
- Per-tenant role-mapping config tables (replacing the hardcoded `admin/member/viewer`).
- A Scope3-native UI built on the API (replacing the proxied salesagent UI).
- Open-instance migration tooling (move existing direct-customer tenants into managed mode).
