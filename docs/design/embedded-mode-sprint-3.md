# Sprint 3 Spec: Workflow Mutations + Detail Read Endpoints

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1](./embedded-mode-sprint-1.md), [sprint 1.5](./embedded-mode-sprint-1.5.md), [sprint 2](./embedded-mode-sprint-2.md)
**Status:** Draft
**Last updated:** 2026-05-04

## Scope

Sprint 1.5 already shipped the consolidated `GET /tenants/{tid}/status` summary endpoint. Sprint 3 adds:

1. **Workflow approve/reject mutations** — Scope3 wants these in its own UI rather than sending users into the salesagent UI for approvals.
2. **Detail read endpoints** behind the status summary — workflow detail, media-buy list/detail, audit-log search, sync history. The status endpoint surfaces aggregates; these endpoints back the drill-downs.

8 endpoints:

```
# Workflows — approve/reject + detail (status endpoint already covers list/summary)
GET     /tenants/{tid}/workflows                      # filterable list (drill-down from status)
GET     /tenants/{tid}/workflows/{wid}                # detail
POST    /tenants/{tid}/workflows/{wid}/approve
POST    /tenants/{tid}/workflows/{wid}/reject

# Media buys (read-only — managed externally by buyer agent calls)
GET     /tenants/{tid}/media-buys                     # filterable list
GET     /tenants/{tid}/media-buys/{mbid}              # detail

# Audit log
GET     /tenants/{tid}/audit-log

# Sync (GAM)
GET     /tenants/{tid}/sync-history
```

`GET /status` (sprint 1.5) covers the summary view; sprint 3 endpoints are the drill-downs. There is intentional redundancy between `status.workflows` (counts) and `GET /workflows` (full list) — that's the point: cheap dashboard render vs. detailed browsing.

All endpoints follow the same plumbing as previous sprints: spectree, Pydantic, management-API-key auth, repository delegation.

## Pydantic schemas

### Workflows

```python
class WorkflowSummary(BaseModel):
    workflow_id: str
    workflow_type: str               # "media_buy_approval", "creative_approval", etc.
    status: Literal["pending", "approved", "rejected", "cancelled", "expired"]
    subject_type: str                # "media_buy", "creative", etc.
    subject_id: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    requested_by_principal_id: str | None
    requested_by_principal_name: str | None

class WorkflowDetail(WorkflowSummary):
    description: str
    context: dict                    # flexible payload (request details, diffs, etc.)
    decisions: list[WorkflowDecision]  # history of approve/reject events

class WorkflowDecision(BaseModel):
    decided_at: datetime
    decision: Literal["approve", "reject"]
    decided_by_email: str | None
    decided_by_source: str           # "scope3_storefront", "salesagent_ui", "management_api"
    notes: str | None

class ListWorkflowsResponse(BaseModel):
    workflows: list[WorkflowSummary]
    count: int
    next_cursor: str | None          # opaque cursor for pagination

class ApproveWorkflowRequest(BaseModel):
    notes: str | None = None

class RejectWorkflowRequest(BaseModel):
    notes: str = Field(..., min_length=1)  # rejection always requires a reason
```

`POST .../approve` and `.../reject` record the decision and trigger any downstream effects (e.g., approving a media buy unblocks its delivery). Decisions are tied to the `X-Identity-Email` header for audit; if the header is absent (Tenant Management API call by control plane), recorded as `decided_by_source="management_api"`.

### Media buys

```python
class MediaBuySummary(BaseModel):
    media_buy_id: str
    buyer_ref: str | None            # caller's reference
    principal_id: str
    principal_name: str
    status: Literal["pending_approval", "active", "paused", "completed", "cancelled", "failed"]
    flight_start_date: date
    flight_end_date: date
    total_budget: Decimal
    currency: str
    delivered_impressions: int | None
    delivered_spend: Decimal | None
    pacing: Literal["on_pace", "underpacing", "overpacing"] | None
    created_at: datetime

class MediaBuyDetail(MediaBuySummary):
    products: list[str]              # product IDs
    targeting: dict | None
    creatives: list[str]             # creative IDs
    status_history: list[StatusEvent]

class ListMediaBuysResponse(BaseModel):
    media_buys: list[MediaBuySummary]
    count: int
    next_cursor: str | None
```

Filter params: `?status=`, `?principal_id=`, `?from_date=`, `?to_date=`, `?limit=N&cursor=...`.

**No POST/PATCH/DELETE on media buys.** Buys are owned by the buyer protocol (MCP/A2A); the API only reads them. If Scope3 needs to cancel a buy on behalf of a customer, that's a buyer-agent action, not a tenant-management one.

### Audit log

```python
class AuditLogEntry(BaseModel):
    audit_log_id: str
    occurred_at: datetime
    action: str                      # "tenant.update", "principal.create", "workflow.approve", etc.
    subject_type: str
    subject_id: str
    actor_type: Literal["user", "system", "management_api", "super_admin", "buyer_agent"]
    actor_email: str | None          # local User email if applicable
    external_user_email: str | None
    external_user_id: str | None
    external_org_id: str | None
    external_source: str | None
    details: dict                    # before/after diff, full request payload, etc.

class ListAuditLogResponse(BaseModel):
    entries: list[AuditLogEntry]
    count: int
    next_cursor: str | None
```

Filter params: `?action_prefix=`, `?subject_type=`, `?subject_id=`, `?actor_type=`, `?external_source=`, `?from_date=`, `?to_date=`, `?limit=N&cursor=...`.

`action_prefix` matches the dotted action name (e.g., `?action_prefix=workflow.` returns all workflow events). Useful for compliance queries.

### Sync history

Current sync state already lives in the consolidated `GET /status` endpoint (sprint 1.5). Sprint 3 only adds the historical timeline.

```python
class SyncRunInfo(BaseModel):
    sync_id: str
    sync_type: Literal["inventory", "custom_targeting", "advertisers"]
    started_at: datetime
    completed_at: datetime | None
    status: Literal["success", "failed", "in_progress", "cancelled"]
    duration_seconds: int | None
    items_processed: int
    items_failed: int
    error_summary: str | None

class ListSyncHistoryResponse(BaseModel):
    runs: list[SyncRunInfo]
    count: int
    next_cursor: str | None
```

Filter params: `?sync_type=inventory&status=failed`, `?limit=N&cursor=...`. Default `limit=20`. Used by Scope3 to render a sync timeline / health graph.

## Pagination

Sprint 3's list endpoints all use **cursor-based pagination**:
- `?limit=N` — page size, default 50, max 500.
- `?cursor=<opaque>` — opaque token; obtain from previous response's `next_cursor`.
- Last page returns `next_cursor=null`.

Cursor is a base64-encoded encoded `(timestamp, id)` tuple to avoid skipping rows when new entries are inserted between fetches. Standard pattern; existing codebase has examples.

Why not offset-based: audit logs and workflows can grow large, and offset-based pagination silently drops/duplicates rows when concurrent inserts happen.

## Endpoint behavior (notes)

| Endpoint | Notes |
|---|---|
| `GET /workflows` | Default sort: pending first, then by `created_at` desc. |
| `POST /workflows/{wid}/approve\|reject` | Idempotent: re-approving an already-approved workflow returns 200 with the existing state, not a new decision. |
| `GET /media-buys` | Pacing is computed (delivered ÷ expected-by-now); null if buy hasn't started. |
| `GET /audit-log` | Indexed on `(tenant_id, occurred_at desc, action)`. Heavy queries should use cursor + reasonable date filters. |
| `GET /sync-history` | Historical timeline. Current state is in `GET /status` (sprint 1.5). |

## Error responses

Reuses sprint 1's `ApiError`. New error codes:

| HTTP | code | When |
|---|---|---|
| 404 | `workflow_not_found` | `{wid}` doesn't exist or wrong tenant |
| 404 | `media_buy_not_found` | `{mbid}` doesn't exist or wrong tenant |
| 409 | `workflow_already_decided` | Approve/reject on a non-pending workflow (returns existing state on idempotent re-decide; only fails on conflict, e.g., approve after reject) |
| 409 | `workflow_expired` | Approve/reject after `expires_at` |

## Acceptance criteria

**Workflows:**
- [ ] List filterable by status, sorted with pending first.
- [ ] `approve` records decision with `external_*` fields populated when called via UI proxy with identity headers.
- [ ] `reject` requires non-empty notes (Pydantic validation).
- [ ] Idempotent re-decide returns 200 with existing state; conflicting re-decide returns 409.
- [ ] Expired workflow can't be decided.
- [ ] Approve/reject invalidates the `GET /status` cache so the workflow count drops on next status fetch.

**Media buys:**
- [ ] No write methods exposed.
- [ ] Filter params combine correctly (status + principal + date range).
- [ ] Pacing computed correctly for active buys; null for not-yet-started.

**Audit log:**
- [ ] `action_prefix` filter matches dotted prefixes correctly.
- [ ] `external_*` filter params work for embedded-tenant searches.
- [ ] Cursor pagination doesn't skip or duplicate entries when new audit rows are inserted between calls.

**Sync history:**
- [ ] Returns chronologically with cursor pagination.
- [ ] `sync_type` filter works.
- [ ] Historical entries match the most-recent values surfaced in `GET /status` (sprint 1.5).

**Integration with prior sprints:**
- [ ] Provision a embedded tenant; trigger a sync via existing `sync_api`; `GET /sync-history` includes the run; `GET /status` reflects current state.
- [ ] Create a workflow (via internal mechanism — workflows are created by buyer protocol, not API); approve via API; status flips; audit log records the decision; `GET /status.workflows.open_count` decreases on next fetch.

**OpenAPI:**
- [ ] All 8 endpoints in the spec.
- [ ] Filter params documented for every list endpoint.

## Open questions

1. **Multi-currency budget aggregation.** Media-buy summaries can include buys in different currencies. Options: (a) convert to a single reporting currency using a configurable rate, (b) return per-currency breakdown, (c) report only the tenant's default currency. Decide at implementation time based on Scope3's UI needs.
2. **Workflow expiration policy.** Today's workflows may not have `expires_at`. If Scope3 wants SLA-driven auto-expiration, that's a separate feature — sprint 3 reads whatever the existing workflow model has.
3. **Audit log retention.** Long-running tenants accumulate large audit logs. Retention policy (e.g., keep 90d hot, archive older) isn't sprint 3 work but is worth noting — Scope3 may need a separate archive query interface.

## What sprint 4+ builds on this

After sprints 1, 1.5, 2, 3, the embedded-mode salesagent is feature-complete for Scope3's launch. Remaining sprints are optional:

- **Sprint 4 (optional)**: publisher-managed CRUD via API (principals, products) — automation conveniences for bulk operations.
- **Sprint 5 (optional)**: remaining publisher-managed sub-resources (tags, properties, profiles, etc.) via API.
- **Sprint 6 (optional)**: outbound webhooks — sync failed, workflow created, media buy delivered, adapter connection lost. Reduces polling load on `GET /status` and `GET /workflows`.

After sprint 6, the embedded-mode integration is complete: provisioning, configuration, runtime, observability, async notifications. Everything Scope3 needs to embed the salesagent as a managed service.
