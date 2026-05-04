# Sprint 1.5 Spec: Storefront Integration Essentials

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1](./managed-tenant-mode-sprint-1.md)
**Status:** Draft
**Last updated:** 2026-05-04

## Scope

Sprint 1.5 lands the three small things Scope3 needs to ship the Storefront integration end-to-end:

1. **`POST /tenants/preview-adapter`** — pre-provision adapter test that returns network metadata. Lets the Storefront UI confirm the GAM service-account grant + auto-fill currency/timezone before committing to a tenant.
2. **`GET /tenants/{tid}/status`** — consolidated operational status. Replaces what would have been per-domain summary endpoints (sync, workflows, media-buys, etc.) — one round-trip, one place to tune caching.
3. **Identity-propagation contract sign-off** — lock the `X-Identity-*` header schema and `IDENTITY_TRUST_MODE=network` as a stable integration spec at `docs/integration/embedded-mode-identity-contract.md`.

This sprint exists because Scope3 needs status visibility from day one to surface in the Storefront tenant overview, and adapter preview meaningfully improves the provisioning UX. Publisher-CRUD-via-API (sprints 4–5 in the new phasing) is no longer time-critical because publishers operate via the proxied UI.

3 endpoints + 1 contract doc.

## `POST /api/v1/tenant-management/tenants/preview-adapter`

Same `AdapterConfig` discriminated union as sprint 1's `POST /tenants/provision`. No persistence. Calls the adapter's `test_connection()` plus a metadata fetch.

### Schemas

```python
class PreviewAdapterRequest(BaseModel):
    adapter: AdapterConfig    # GAM | Mock (same union as provision)

class PreviewAdapterResponse(BaseModel):
    ok: bool
    network_name: str | None = None
    network_code: str | None = None
    currency_code: str | None = None      # ISO 4217 from network metadata
    time_zone: str | None = None          # IANA tz from network metadata
    advertiser_count: int | None = None   # null if cheap fetch unavailable
    inventory_reachable: bool             # could we list at least one ad unit?
    error: str | None = None              # populated when ok=false
```

### Behavior

1. Validate request schema.
2. Call adapter's `test_connection()`. If it fails, return 200 with `ok=false`, `inventory_reachable=false`, and `error` populated. Do **not** return 4xx — Storefront wants to render this inline, not trigger error handling.
3. If connection succeeds, fetch network metadata (one extra GAM API call: `getCurrentNetwork()`).
4. Optionally fetch `advertiser_count` if cheap (one paginated `getAdvertisersByStatement` with `LIMIT 1` returning total). If the call would take >2s or isn't available on the adapter, return `null`.
5. Return 200 with populated fields.

### Why 200 on failure

Adapter creds being wrong is a normal flow, not an exception. The Storefront UX is "publisher pastes creds → sees inline 'this works / this doesn't'" — a 4xx would route through error-handling middleware and likely surface as a generic error. 200 + `ok=false` keeps it predictable.

The hard 4xx cases (malformed request body, missing API key) still return their normal error codes via the existing middleware.

### Implementation notes

- The GAM adapter today supports `test_connection()` (sprint 1 scope item). It also supports network-metadata calls; needs to be confirmed the metadata-fetch is exposed cleanly. If not, add a small `get_network_metadata()` method.
- `advertiser_count` is a nice-to-have. If the GAM read for it costs more than ~500ms, drop it from v1 — Scope3 confirmed "whatever's easy."
- For the Mock adapter, return canned values (`network_name="Mock Network"`, `currency_code="USD"`, etc.) so dev environments work.

## `GET /api/v1/tenant-management/tenants/{tid}/status`

The consolidated tenant operational snapshot. One call, one round-trip, one cache lifetime.

### Schemas

```python
class AdapterStatus(BaseModel):
    type: str                              # "google_ad_manager", "mock", etc.
    connected: bool
    last_tested_at: datetime | None
    last_test_error: str | None

class SyncRunStatus(BaseModel):
    last_run_at: datetime | None
    status: Literal["success", "failed", "running", "never_run"]
    item_count: int | None
    error: str | None

class SyncsStatus(BaseModel):
    inventory: SyncRunStatus
    custom_targeting: SyncRunStatus
    advertisers: SyncRunStatus

class WorkflowsStatus(BaseModel):
    open_count: int
    oldest_opened_at: datetime | None      # null if no open workflows
    by_kind: dict[str, int]                # {"creative_review": 3, "media_buy_approval": 1}

class MediaBuysStatus(BaseModel):
    """Top-level transaction records. A media buy contains 1+ packages."""
    active_count: int
    pending_approval_count: int

class PackagesStatus(BaseModel):
    """AdCP packages — the line-items running inside media buys."""
    active_count: int
    paused_count: int
    last_24h_impressions: int

class CreativesStatus(BaseModel):
    active_count: int
    pending_review_count: int
    rejected_last_24h_count: int

class WebhooksStatus(BaseModel):
    """Populated only after sprint 6 ships outbound webhooks. Null until then."""
    last_24h: dict                         # {"delivered": int, "failed": int, "success_rate": float}
    last_failure_at: datetime | None

class TenantStatusResponse(BaseModel):
    adapter: AdapterStatus
    syncs: SyncsStatus
    workflows: WorkflowsStatus
    media_buys: MediaBuysStatus
    packages: PackagesStatus
    creatives: CreativesStatus
    webhooks: WebhooksStatus | None        # null until sprint 6
    fetched_at: datetime
```

All field names snake_case; Scope3 maps to camelCase at its edge.

### Behavior

1. Single request → single response.
2. Computed view, not a stored row. Repository fetches each block via existing data sources:
   - `adapter` from `AdapterConfig` + last connection test result (sprint 1 stores this).
   - `syncs.*` from `gam_sync_runs` (or whatever the existing table is — confirm name during impl).
   - `workflows` from `workflow_steps` joined to `object_workflow_mappings`.
   - `media_buys`, `packages` from `media_buys` table joined to `media_buy_packages`.
   - `creatives` from `creatives` table.
   - `webhooks` from `webhook_deliveries` (sprint 6 table; return null until then).
3. Aggressive caching: 5–10s server-side cache keyed by `tenant_id`. Storefront may call this on every page render, so cache invalidation is critical — bust on adapter test, sync completion, workflow state change, etc.
4. Single round-trip means cache invalidation is also single-keyed.

Why one endpoint vs. several: Scope3 explicitly requested consolidation. Per-domain endpoints exist separately for *detail* views (sprint 3 keeps `GET /workflows`, `GET /media-buys`, etc. for drill-downs) but the homepage card needs aggregate view in one call.

### Caching

```python
@cached(ttl=10, key=lambda tid: f"tenant_status:{tid}")
def get_tenant_status(tid: str) -> TenantStatusResponse:
    ...

# Invalidation hooks:
# - adapter test (any) → invalidate
# - sync run state change → invalidate
# - workflow state change → invalidate
# - media buy / package state change → invalidate
# - creative submitted/approved/rejected → invalidate
# - webhook delivery (sprint 6) → invalidate
```

Use the existing cache infrastructure if any (Redis? In-memory?). If none, in-memory is fine for sprint 1.5 — single-process is the dev/staging norm; multi-process correctness can come later.

## Identity-propagation contract sign-off

Write `docs/integration/embedded-mode-identity-contract.md` as a stable, versioned integration spec — separate from the design doc so Scope3 has a single canonical reference and can wire its edge middleware with a stable contract. Content covers:

- Header schema (the 6 headers from the design)
- Role enum: `admin | member | viewer`
- `IDENTITY_TRUST_MODE = network | signed` config
- For Scope3's deployment: `IDENTITY_TRUST_MODE = network`, no signature required
- Versioning: `v1` is the current; breaking changes get `v2` field added; non-breaking additions don't bump
- Failure modes: missing required headers → 403 `identity_required`; org claim doesn't match URL tenant → 403 `identity_org_mismatch`

The contract doc is "this is final, ship the edge middleware against it." See [docs/integration/embedded-mode-identity-contract.md](../integration/embedded-mode-identity-contract.md).

## Acceptance criteria

**Preview adapter:**
- [ ] Valid GAM creds: returns `ok=true` with `network_name`, `network_code`, `currency_code`, `time_zone` populated, `inventory_reachable=true`.
- [ ] Invalid GAM creds: returns 200 with `ok=false`, `inventory_reachable=false`, `error` populated. Not 4xx.
- [ ] Mock adapter: returns canned values.
- [ ] No tenant row created as a side effect (verified by counting tenants before and after).
- [ ] Malformed request body: 422 (Pydantic), not 200 with `ok=false`.

**Status:**
- [ ] All blocks populated with correct values for a tenant with adapter, sync history, open workflows, active media buys, packages, creatives.
- [ ] `webhooks` is `null` until sprint 6 lands.
- [ ] Repeated calls within 10s return cached result (verified by latency or cache-hit metric).
- [ ] Cache invalidates on adapter test, sync state change, workflow state change.
- [ ] Tenant with no activity returns sensible defaults (zero counts, null timestamps) rather than errors.

**Identity contract:**
- [ ] `docs/integration/embedded-mode-identity-contract.md` exists and is versioned.
- [ ] Contains schema, role enum, config knob, failure modes.
- [ ] Marked stable / final.

**OpenAPI:**
- [ ] Both new endpoints in the spec at `/api/v1/tenant-management/openapi.json`.
- [ ] Swagger UI executable for both.

## Open questions

1. **Cache infrastructure.** Does the salesagent have Redis or just in-memory caching? Affects cross-process correctness of the status cache. In-memory is fine for now; flag for ops review.
2. **Sync table names.** Sprint 1.5 references `gam_sync_runs` (or similar) to populate `syncs.inventory`, etc. Confirm the actual table/column names during impl. If sync state lives elsewhere (e.g., `audit_logs`), repository will need a different query.
3. **`packages.last_24h_impressions`.** This requires querying delivery data which may live in a separate table (`media_buy_deliveries` or similar). If that table doesn't exist or isn't kept fresh, return `0` and flag as a known gap rather than blocking.
4. **`creatives.rejected_last_24h_count`.** Same data-availability question — depends on creative-review history table.

## What sprint 2 builds on this

Sprint 2 (runtime hardening) ships the UI middleware, network policy, and `resolve_identity()` change for embedded-mode MCP/A2A — closing out the operational-safety side. Sprint 1 + 1.5 + 2 = embedded mode is fully operational and observable; publishers can do their work via the proxied UI; Scope3 has the API automation surface for everything Scope3 cares about.

Sprints 4–5 (publisher CRUD via API) become opt-in convenience features for bulk operations / future automation. They're no longer prerequisite for Scope3's launch.
