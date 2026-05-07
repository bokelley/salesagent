# Sprint 1.8 Spec: Buyer-Advertiser Routing for Embedded Storefronts

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1.6](./sync-accounts-advertiser-mapping.md), [sprint 1.7](./replace-authorized-properties-with-aao-lookup.md)
**Status:** Shipped (see [Addendum](#addendum-auto_provision_advertisers-retained-flag-not-dropped) re: `auto_provision_advertisers` flag retention).
**Update (PR #78, May 2026):** §6 references to `Tenant.house_domain` are obsolete —
the column was dropped. Per-publisher `PublisherPartner.publisher_domain` rows are
the only "house" concept now. The `public_agent_url` validation + embedded-tenant
write guards in §6 still apply.
**Last updated:** 2026-05-04

> **Note on item 6 below.** Sprint 1.8 originally planned to drop `auto_provision_advertisers` as redundant (item 6 in the Migrations section). That decision has been **reversed** — see [Addendum](#addendum-auto_provision_advertisers-retained-flag-not-dropped). The flag stays.

## Scope

Closes the agent-billed loop on embedded-mode storefronts: every buy that lands on a managed publisher gets attributed to the right GAM advertiser based on the buyer's `(operator, brand)` context, with publisher-controlled overrides and a tenant default for the unmatched case.

Five required pieces + two optional:

1. **`Tenant.default_gam_advertiser_id`** — required-before-activation fallback advertiser. Replaces Sprint 1.6's `auto_provision_advertisers` flag (cleaner: explicit fallback wins over implicit auto-create).
2. **`advertiser_routing_rules` table** — ordered overrides keyed by `(operator_domain, brand_house, brand_id)` with null-as-wildcard. Resolution precedence: exact → house wildcard → operator wildcard → tenant default → reject.
3. **Two read endpoints** — paginated/searchable `/gam/advertisers` (powers UI pickers; large GAM networks have 10k+ companies) + `/recent-buyers` rollup with `resolved_via` per row (lets publishers spot fall-through buyers landing on the default).
4. **§5 preview-adapter advertisers extension** *(optional)* — onboarding flow gets advertisers in the same round trip that confirms the GAM grant.
5. **§6 platform-managed lock-down** — `house_domain` + `public_agent_url` (sprint 1.7 fields) become read-only in the publisher UI when `tenant.is_embedded=true`; model-layer write guard enforces.
6. **§7 `setup_tasks` block on `/status`** — folds the existing setup checklist into the status response with `scope: platform | publisher` annotation so Storefront can route gaps correctly (escalate "platform" gaps to itself; deep-link "publisher" gaps into the iframe).
7. **§8 auto-run syncs + collapsed refresh** — per-tenant `sync_cadence_minutes`, first-sync on provision, single `POST /refresh` endpoint replaces N per-sync triggers in the publisher UI.

## Vocabulary

The spec text uses "buyer-advertiser-mappings" everywhere; the storage shape is a precedence-ordered routing chain with wildcards. Reconciling:

| Layer | Name | Why |
|---|---|---|
| Storage table | `advertiser_routing_rules` | The impl IS routing rules: ordered, precedence-based, wildcards |
| Pydantic schema | `BuyerAdvertiserMapping` | Matches Storefront UI vocabulary + spec text |
| API path | `/buyer-advertiser-mappings` | Same — Storefront calls this from a "buyer routing" widget |

Internal code uses `routing_rule` / `RoutingRule`; external surface uses `buyer_advertiser_mapping`. One-line mapping at the boundary.

## Schema

### New tenant column

```python
Tenant.default_gam_advertiser_id: str | None  # nullable for un-activated tenants
```

Validation on PATCH/PROVISION: must reference a synced `Advertiser.id` for the tenant's GAM network. The actual activation gate lives at the Tenant Management API layer ("can't deactivate the `pending` flag without a default set"); the column itself stays nullable so legacy / open-instance tenants don't need backfill.

### New table

```sql
CREATE TABLE advertiser_routing_rules (
    id              VARCHAR(40) PRIMARY KEY,            -- "rule_<random>"
    tenant_id       VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    operator_domain VARCHAR(255) NOT NULL,              -- never null — every rule is operator-scoped
    brand_house     VARCHAR(255),                       -- null = "any house under operator"
    brand_id        VARCHAR(255),                       -- null = "any brand under house"
    gam_advertiser_id VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Uniqueness: one rule per (tenant, operator, brand_house, brand_id) tuple.
    -- Postgres treats NULLs as distinct in unique constraints by default, but
    -- we want NULL == NULL for wildcard matching, so the constraint uses
    -- COALESCE-equivalent expression indexes.
    CONSTRAINT uq_routing_rule_natural_key UNIQUE (
        tenant_id, operator_domain,
        COALESCE(brand_house, ''),
        COALESCE(brand_id, '')
    )
);

CREATE INDEX idx_routing_rules_tenant ON advertiser_routing_rules(tenant_id);
CREATE INDEX idx_routing_rules_operator ON advertiser_routing_rules(tenant_id, operator_domain);
```

The natural-key uniqueness uses `COALESCE(.., '')` so `NULL` participates in uniqueness — without that, a publisher could create N duplicate "any-brand" rules under the same operator, which the precedence chain can't disambiguate.

`gam_advertiser_id` validation lives at the API layer (must reference a synced `Advertiser.id`) — keeping it out of the DB constraint avoids tight coupling between this table and the GAM advertiser cache.

## Resolution precedence

Wired into `_create_media_buy_impl` after Sprint 1.6's existing Account lookup. Pseudo-code:

```python
def resolve_advertiser_for_buy(tenant_id, account_ref):
    # Sprint 1.6 fast-path: existing Account with attached advertiser wins.
    if account_ref.is_account_id_form():
        account = lookup_by_id(account_ref.account_id)
        if account.platform_mappings.gam_advertiser_id:
            return account.platform_mappings.gam_advertiser_id, "account"

    # 0. Sandbox carve-out (Q4 decision): sandbox traffic NEVER touches
    #    routing rules or the tenant default — always goes to the
    #    per-tenant sandbox advertiser (lazy-created in sprint 1.6).
    #    Don't bill, don't pollute reports, don't count against inventory.
    if account_ref.sandbox:
        sandbox_id = ensure_sandbox_advertiser(tenant_id)  # sprint 1.6 helper
        return sandbox_id, "sandbox"

    # Inline form (operator + brand) → run the precedence chain.
    operator = account_ref.operator
    brand_house = account_ref.brand.domain
    brand_id = account_ref.brand.brand_id  # may be None

    # 1. Exact match (operator + brand_house + brand_id).
    if brand_id and (rule := find_rule(tenant_id, operator, brand_house, brand_id)):
        return rule.gam_advertiser_id, "exact"

    # 2. House wildcard (operator + brand_house + null).
    if rule := find_rule(tenant_id, operator, brand_house, None):
        return rule.gam_advertiser_id, "house"

    # 3. Operator wildcard (operator + null + null).
    if rule := find_rule(tenant_id, operator, None, None):
        return rule.gam_advertiser_id, "operator"

    # 4. Tenant default.
    tenant = lookup_tenant(tenant_id)
    if tenant.default_gam_advertiser_id:
        return tenant.default_gam_advertiser_id, "default"

    # 5. No fallback configured → reject (Q3 decision: activation is
    #    implicit; the buyer-protocol error path IS the contract).
    raise AdCPError(
        "TENANT_NOT_ACTIVATED",
        message=f"Tenant {tenant_id!r} has no default_gam_advertiser_id and no "
                f"matching routing rule for ({operator}, {brand_house}, {brand_id}). "
                "Publisher must set a default advertiser before this tenant can buy media.",
    )
```

`resolved_via` (the second tuple element) is recorded on the auto-created Account so `/recent-buyers` can surface it without re-running the chain.

### Auto-Account creation

First buy from a `(operator, brand_house, brand_id)` triple that doesn't have an existing Account row creates one with the resolved `gam_advertiser_id` already attached. Subsequent buys reuse the same Account (Sprint 1.6's natural-key lookup hits it). `Account.platform_mappings.google_ad_manager.advertiser_id` becomes the persistent record; the routing rules are the policy that *decides* what advertiser_id to put there on first creation.

This makes Sprint 1.6's `auto_provision_advertisers` flag redundant. Recommend removing it as part of this sprint — the routing chain is more explicit and replaces both the auto-create-from-rules path AND the explicit-default-fallback path.

## API surface

Under `/api/v1/tenant-management/`, same `X-Tenant-Management-API-Key` auth.

### Tenant default advertiser

- **`POST /tenants/provision`** — `default_gam_advertiser_id` is optional in the body (publishers can set it after provisioning). Validated against synced advertisers when present.
- **`PATCH /tenants/{tid}`** — supports `default_gam_advertiser_id`.
- **`GET /tenants/{tid}`** — returns it on `TenantDetail`.

### Routing rule CRUD

```
GET    /tenants/{tid}/buyer-advertiser-mappings
       → { "mappings": [BuyerAdvertiserMapping], "count": int }

POST   /tenants/{tid}/buyer-advertiser-mappings
       body: { operator_domain, brand_house?, brand_id?, gam_advertiser_id }
       → BuyerAdvertiserMapping (201)
       400: invalid_advertiser_id     — gam_advertiser_id not in synced advertisers
       409: routing_rule_conflict     — duplicate (operator, brand_house, brand_id) tuple

PATCH  /tenants/{tid}/buyer-advertiser-mappings/{mapping_id}
       body: any of { brand_house, brand_id, gam_advertiser_id }
       (operator_domain is immutable — change requires DELETE + POST)
       → BuyerAdvertiserMapping

DELETE /tenants/{tid}/buyer-advertiser-mappings/{mapping_id}
       → 204
       404: mapping_not_found
```

`BuyerAdvertiserMapping` Pydantic shape matches the spec exactly (`operator_domain`, `brand_house?`, `brand_id?`, `gam_advertiser_id` + `id`, `created_at`, `updated_at`). No internal-only fields exposed.

### Read endpoints for the routing widget

```
GET /tenants/{tid}/gam/advertisers
    Query: q (string, optional), limit (int, default 50, max 500), cursor (string, optional)
    → {
        "advertisers": [{"id", "name", "currency_code", "status"}],
        "next_cursor": str | null,
        "synced_at": datetime
      }
```

Search runs against the salesagent's local synced copy (`gam_advertisers` cache table — populated by the existing sync_all_tenants cron). `q` is case-insensitive substring match against `name` OR exact match against `id`. Min 2 chars (under that, return first page unfiltered). Cursor is opaque base64-encoded offset (matches the existing pagination pattern in `_apply_pagination` from `accounts.py`).

```
GET /tenants/{tid}/recent-buyers?days=30&limit=100
    → {
        "buyers": [{
          "operator_domain", "brand_house", "brand_id",
          "last_seen_at", "request_count",
          "resolved_gam_advertiser_id", "resolved_via"
        }]
      }
```

Source data: `Account` rows for embedded-mode tenants — each Account already carries `(operator, brand_house, brand_id)` from sync_accounts upserts AND the resolved `platform_mappings.google_ad_manager.advertiser_id`. We need to add `Account.resolved_via` (one of `"account" | "sandbox" | "exact" | "house" | "operator" | "default"`) at first-creation time, and aggregate `request_count` / `last_seen_at` from `MediaBuy` rows joined to Account.

`get_products` requests carry `account: AccountReference | None` too — when populated, they hit the same routing chain. Recent-buyers therefore covers both flows uniformly when the buyer supplies the field.

### §5: preview-adapter extension

```
POST /tenants/preview-adapter
    body: { adapter: AdapterConfig }   (unchanged)
    response gains: advertisers: list[GamAdvertiser]   (same shape as /gam/advertisers, no pagination — preview is small)
```

Single round trip during onboarding: publisher pastes GAM creds → modal confirms the connection AND populates the default-advertiser dropdown. Per the spec, "one round-trip rather than two."

Cap at first 100 advertisers without pagination — the preview is for confirming reachability, not browsing. Full searchable list is `/gam/advertisers`.

## §6: Platform-managed `house_domain` + `public_agent_url`

Sprint 1.7 added the fields and made them optional on PATCH/PROVISION. Sprint 1.8 lands the platform-managed lock-down per the sprint-1 platform-vs-publisher split.

### Validation

Pydantic field validators on `ProvisionTenantRequest` and `UpdateTenantRequest`:

- `house_domain` — bare domain (no scheme, no path). Regex: `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$`. Reject `https://wonderstruck.org`, `wonderstruck.org/foo`, `WONDERSTRUCK.ORG` (force-lowercase before validate). Length cap: 253 chars (DNS).
- `public_agent_url` — valid HTTPS URL. Use Pydantic's `HttpUrl` with a custom validator rejecting `http://` (only `https://` accepted). No path-component restriction — `https://interchange.io` and `https://buyer.scope3.com/agent` are both valid.

### Lock-down via the model-layer write guard

`src/core/database/embedded_tenant_guard.py` (sprint 1) blocks UI-driven writes to platform-managed columns when `tenant.is_embedded=true` AND the session isn't flagged with `info["management_api_caller"]`. Today's protected set on `Tenant` is empty (the comment notes "no publisher-writable platform-managed columns").

Add `house_domain` and `public_agent_url` to the protected set. Result:
- `PATCH /tenants/{tid}` (Tenant Management API, sets `info["management_api_caller"]=True`) → writes succeed.
- Publisher-facing Settings UI POST → write attempt raises `EmbeddedTenantWriteError`, surfaced as 403 with the existing `embedded_tenant_write_blocked` error code.

### Settings page UX

`templates/tenant_settings.html` (or wherever the Settings form lives) gains:

```jinja
{% if tenant.is_embedded %}
<div class="alert alert-info">
  <i class="fas fa-lock"></i>
  These values are platform-managed by {{ tenant.external_source | default('your platform') | title }}.
  Contact your account team to change them.
</div>
{% endif %}

<input type="text"
       name="house_domain"
       value="{{ tenant.house_domain }}"
       {% if tenant.is_embedded %}readonly{% endif %}>
```

The `readonly` attribute is cosmetic (DOM-side only) — the model-layer guard is what enforces. Even if a publisher bypasses the form (e.g. crafted POST), the guard still rejects.

### Setup-checklist hide-when-set

Sprint 1.7 added "Publisher House Domain" + "Public Agent URL" as the first two critical-tasks items. Sprint 1.8 hides them when both are populated AND tenant is embedded:

```python
# src/services/setup_checklist_service.py:_check_critical_tasks
if not (tenant.is_embedded and tenant.house_domain and tenant.public_agent_url):
    tasks.append(SetupTask(key="house_domain", ...))
    tasks.append(SetupTask(key="public_agent_url", ...))
```

Open-instance tenants always see both tasks (today's behavior). Embedded tenants with both fields set never see them — those decisions live with the host product, not the publisher. The "Authorized Properties" task already short-circuits to "brand.json reachability probe" when `house_domain` is set (Sprint 1.7), so it's effectively hidden once the platform pre-fills `house_domain`.

### Backfill

No migration needed beyond Sprint 1.7's existing nullable columns. Existing embedded-mode tenants get `house_domain` + `public_agent_url` populated by the host product via PATCH; legacy open-instance tenants keep NULL and continue to see the setup tasks.

## §7: `setup_tasks` block on `GET /tenants/{tid}/status`

Sprint 1.5's status endpoint surfaces operational state (adapter, syncs, workflows, media_buys, packages, creatives, webhooks). It does NOT surface configuration completeness — Storefront has to call a separate path to render the homepage checklist.

§7 folds the existing `setup_checklist_service` output into the `/status` response, with `scope` annotation so Storefront can route gaps correctly:

```python
class SetupTaskItem(BaseModel):
    id: str                        # "house_domain", "default_advertiser", etc.
    name: str                      # "Publisher House Domain"
    severity: Literal["blocker", "warning", "info"]
    scope: Literal["platform", "publisher"]
    description: str
    configure_path: str | None     # deep-link into the relevant settings page

class SetupTasksBlock(BaseModel):
    blocker_count: int
    warning_count: int
    items: list[SetupTaskItem]
```

`TenantStatusResponse` (Sprint 1.5) gains `setup_tasks: SetupTasksBlock` alongside the existing operational blocks.

### Severity mapping

`SetupTask.is_complete` → severity:
- complete = `"info"` (or omit from items entirely — Storefront UI tradeoff, recommend keep + render as "✓")
- incomplete + critical-tasks tier = `"blocker"`
- incomplete + recommended-tasks tier = `"warning"`
- incomplete + optional-tasks tier = `"info"`

The existing `_check_critical_tasks` / `_check_recommended_tasks` / `_check_optional_tasks` split in `setup_checklist_service.py` already drives this — wire severity off the calling tier.

### Scope mapping

Per the sprint-1 platform-vs-publisher split:

| Task | scope (embedded) | scope (open-instance) |
|---|---|---|
| `house_domain` | `platform` (host product sets at provision) | `publisher` |
| `public_agent_url` | `platform` | `publisher` |
| `default_gam_advertiser_id` (from §1) | `publisher` | `publisher` |
| `ad_server_connected` | `publisher` (creds belong to the operator) | `publisher` |
| `currency_limits` | `publisher` | `publisher` |
| `sso_configuration` | hidden (SSO managed by upstream platform) | `publisher` |
| `authorized_properties` (legacy) | hidden (deprecated; brand.json drives in §1.7) | `publisher` |

`scope=platform` items in an embedded tenant signal "the host product didn't finish provisioning" — the host's UI should escalate that internally, not expose to the publisher. `scope=publisher` items deep-link via `configure_path` into the iframe at the right Settings tab.

### `configure_path` shape

Relative to the tenant root (so it composes cleanly with the storefront's iframe prefix). Examples:
- `/settings#aao` for AAO config (house_domain, public_agent_url)
- `/settings#advertiser-routing` for default advertiser (added in §1)
- `/settings#adserver` for ad server config

Storefront prepends its iframe prefix (`/storefront/psa/tenant/<id>`) when rendering deep-links.

### Caching

Sprint 1.5's status cache (5s TTL, per-tenant) covers the new block automatically — `setup_tasks` is computed in the same `_build_status` function and shares the cache key. Invalidation hooks Sprint 1.5 already wired (adapter test, PATCH, deactivate/reactivate) cover the relevant config-change events.

## §8: Auto-run syncs + collapsed refresh button

Sprint 1.5's status endpoint exposes per-sync state (`syncs.inventory`, `syncs.custom_targeting`, `syncs.advertisers`); Sprint 1.5 + crontab already auto-run them every 6h via `sync_all_tenants.py`. What's missing for embedded-mode UX:

1. **Configurable per-tenant cadence** so publishers with high-volume catalogs can pull more frequently (or low-volume publishers can save cron load).
2. **First-sync-on-provision** so an embedded tenant has data the moment the host finishes provisioning (no "wait 6 hours").
3. **Single `POST /tenants/{tid}/refresh` endpoint** that fires all enabled syncs together — Storefront's UI collapses N "Sync inventory" / "Sync targeting" / "Sync advertisers" buttons into one.

### Schema

New nullable column on `Tenant`:

```python
sync_cadence_minutes: int | None = None  # default in code: 360 (6h)
```

The cron driver (`sync_all_tenants.py`) reads this column when picking which tenants to sync this run; tenants with `cadence < 360` get included on the every-N-minute scan, tenants with `cadence > 360` get skipped on intermediate runs.

### `POST /tenants/{tid}/refresh`

```
POST /api/v1/tenant-management/tenants/{tid}/refresh
    → 202 Accepted
    {
      "sync_run_ids": {
        "inventory": "sync_abc123",
        "custom_targeting": "sync_def456",
        "advertisers": "sync_ghi789"
      },
      "started_at": "2026-05-04T17:00:00Z"
    }
```

Spawns one `SyncJob` per enabled sync type (existing table, sprint 1.5 already reads from it for the status block). Each job runs in the existing background worker — endpoint returns immediately with the new run ids so Storefront can poll `GET /status.syncs` for progress.

Idempotent under rapid re-clicks: if a sync of the same type is `running` or started in the last 60 seconds, return the existing run id instead of spawning a duplicate. Avoids hammering GAM when a publisher mashes the button.

### First-sync-on-provision

`POST /tenants/provision` already runs the adapter test before committing the tenant row. On test success, enqueue the same three syncs the manual `/refresh` endpoint runs. Returned response gains `initial_sync.sync_run_ids` (same shape as `/refresh`) so Storefront can show a progress indicator immediately.

### Hide per-sync triggers in embedded-mode UI

`templates/tenant_settings.html` (or wherever sync triggers live today) gets a `{% if not tenant.is_embedded %}` guard around the per-sync buttons. The `Refresh tenant` button calls `POST /refresh` and is shown unconditionally. Open-instance tenants keep today's UI (per-sync buttons + Refresh All).

The `/status.syncs` block stays unchanged — Storefront renders per-sync health from it but exposes only the unified refresh action. Per the spec: "the action surface collapses to one button."

## Migration plan

Five migrations + Sprint 1.6 cleanup:

1. **`add_default_gam_advertiser_id_to_tenant`** — nullable string column. Tenant Management API requires it on embedded-mode activation flows.
2. **`create_advertiser_routing_rules_table`** — schema above. Indexed on (tenant_id) and (tenant_id, operator_domain).
3. **`add_resolved_via_to_account`** — nullable enum (`"account" | "sandbox" | "exact" | "house" | "operator" | "default"`). Backfill is null for legacy rows; new rows populated by the resolution chain.
4. **`add_gam_sandbox_advertiser_id_to_adapter_config`** — Sprint 1.6's deferred sandbox-advertiser cache (Q4 decision: prerequisite for the sprint 1.8 routing chain's sandbox early-return). Nullable; lazy-populated on first sandbox call by `ensure_sandbox_advertiser(tenant_id)`.
5. **`add_sync_cadence_minutes_to_tenant`** — §8 per-tenant sync cadence. Nullable (NULL = use default 360min in code). The cron driver branches on this value when picking tenants per run.
6. **`drop_auto_provision_advertisers_from_tenant`** — Sprint 1.6's flag. Replaced by the routing chain. Deferred until impl is verified end-to-end so we have a rollback option.

### Code changes

- **`src/admin/api_schemas/tenant_management.py`** — `BuyerAdvertiserMapping` (request + response variants), `GamAdvertiser`, `RecentBuyer`, `ListBuyerAdvertiserMappingsResponse`, `ListGamAdvertisersResponse`, `ListRecentBuyersResponse`. Plus `default_gam_advertiser_id` on `ProvisionTenantRequest`, `UpdateTenantRequest`, `TenantDetail`. Plus field validators on `house_domain` (domain regex, force-lowercase) and `public_agent_url` (HTTPS-only).
- **`src/admin/tenant_management_api.py`** — five new endpoints (mapping CRUD: GET/POST/PATCH/DELETE; mapping-list GET, advertisers GET, recent-buyers GET).
- **`src/core/database/embedded_tenant_guard.py`** — add `house_domain` + `public_agent_url` to the `Tenant` protected-columns set. UI writes from embedded tenants raise `EmbeddedTenantWriteError`.
- **`src/services/buyer_advertiser_routing.py`** — new module: `resolve_advertiser_for_buy(tenant_id, account_ref)` runs the precedence chain.
- **`src/core/tools/media_buy_create.py`** — replace Sprint 1.6's `resolve_account_advertiser` call with the new routing service. Account row creation moves into the resolver.
- **`src/services/gam_advertiser_search.py`** — search/paginate the local `gam_advertisers` cache for `/gam/advertisers`.
- **`src/services/recent_buyers_rollup.py`** — joins Account + MediaBuy for `/recent-buyers`.
- **`src/services/setup_checklist_service.py`** — hide `house_domain` + `public_agent_url` tasks when tenant is embedded and both fields are populated. Add `scope` ("platform" | "publisher") + `severity` ("blocker" | "warning" | "info") fields to `SetupTask` for §7. Extend `get_setup_status` to emit the §7-shaped output.
- **`src/admin/services/tenant_status_service.py`** — fold setup_checklist output into `_build_status` as the new `setup_tasks` block (sprint 1.5 cache covers it for free).
- **`templates/tenant_settings.html`** — `readonly` on house_domain + public_agent_url inputs when `tenant.is_embedded`; "Platform-managed by {host product name}" banner (reads `tenant.external_source`). `{% if not is_embedded %}` guard around per-sync trigger buttons (§8); `Refresh tenant` button always visible.
- **`src/admin/tenant_management_api.py`** — `POST /tenants/{tid}/refresh` endpoint (§8). Returns 202 with `sync_run_ids`. 60s idempotency window via the existing `SyncJob.started_at` index.
- **`scripts/sync_all_tenants.py`** — branch on `Tenant.sync_cadence_minutes` per tenant (§8). NULL = 360min default.
- **Optional `src/admin/services/adapter_connection_tester.py`** — extend `AdapterPreview` with `advertisers: list[GamAdvertiser]` field; wire GAM CompanyService.getCompaniesByStatement (limit 100) into `_preview_gam`.

## Acceptance criteria

### Default advertiser
- [ ] `Tenant.default_gam_advertiser_id` migration runs cleanly on staging + dev.
- [ ] `POST /tenants/provision` accepts the field, validates against synced advertisers, persists.
- [ ] `PATCH /tenants/{tid}` updates the field; reject if advertiser_id not in synced advertisers.
- [ ] `GET /tenants/{tid}` returns it on `TenantDetail`.

### Mapping CRUD
- [ ] CRUD endpoints round-trip a mapping with all four field combinations:
   - `(operator, house, brand_id)` exact rule
   - `(operator, house, null)` house wildcard
   - `(operator, null, null)` operator wildcard
   - Reject `(null, ..., ...)` — operator_domain is required.
- [ ] 409 on duplicate `(tenant_id, operator, brand_house, brand_id)` tuple, with NULLs participating in uniqueness.
- [ ] `gam_advertiser_id` referencing a non-synced advertiser → 400.
- [ ] PATCH rejects `operator_domain` changes (must DELETE + POST instead).

### Resolution chain
- [ ] Buy with `account_ref={account_id: "acct_xxx"}` and existing Account → returns the Account's advertiser, `resolved_via="account"`.
- [ ] Buy with `sandbox=true` → returns the per-tenant sandbox advertiser (sprint 1.6 helper), `resolved_via="sandbox"`. Routing rules + tenant default are NOT consulted (Q4 decision).
- [ ] Buy matching exact rule → returns rule's advertiser, `resolved_via="exact"`.
- [ ] Buy matching house wildcard but not exact → `resolved_via="house"`.
- [ ] Buy matching operator wildcard but not house → `resolved_via="operator"`.
- [ ] Buy with no match + tenant has default → `resolved_via="default"`.
- [ ] Buy with no match + tenant has no default → raises `TENANT_NOT_ACTIVATED` (Q3 decision: implicit activation, no separate endpoint). Buyer-protocol error message includes the unresolved `(operator, brand_house, brand_id)` triple so Storefront can surface "publisher hasn't finished setup."
- [ ] First buy with a new triple creates an Account row with the resolved advertiser stamped on `platform_mappings`. Second buy with the same triple reuses the Account.

### Read endpoints
- [ ] `/gam/advertisers?q=acme` returns substring matches against name (case-insensitive) + numeric exact match against id.
- [ ] `/gam/advertisers?cursor=...&limit=50` paginates; `next_cursor` is null on the last page.
- [ ] `/recent-buyers` returns distinct `(operator, brand_house, brand_id)` tuples from the last N days with `resolved_via` per row.
- [ ] Tenants with no recent activity get an empty `buyers: []` array (not 404).

### Preview-adapter extension (optional in this sprint)
- [ ] `POST /tenants/preview-adapter` GAM happy-path returns `advertisers` list with up to 100 entries.
- [ ] Bad creds: `advertisers` is omitted (or empty), `ok=false` with the existing error message — no regression on the existing field set.

### §6 Platform-managed lock-down
- [ ] `POST /tenants/provision` rejects `house_domain` with scheme/path/uppercase (422 with field validator error). Force-lowercase before persist.
- [ ] `POST /tenants/provision` rejects `public_agent_url=http://...` (422); accepts `https://...`.
- [ ] `PATCH /tenants/{tid}` (Tenant Management API) updates both fields successfully on an embedded tenant.
- [ ] Direct DB write to `Tenant.house_domain` outside the management API on a `is_embedded=true` tenant raises `EmbeddedTenantWriteError` (model-layer guard).
- [ ] Setup checklist for embedded tenant with both fields set OMITS the "Publisher House Domain" + "Public Agent URL" critical-tasks items.
- [ ] Settings page renders `readonly` on both fields + "Platform-managed by {host product name}" banner (parametric on `tenant.external_source`) when `tenant.is_embedded=true`.
- [ ] Open-instance (is_embedded=false) tenants always see editable fields + setup tasks (no behavior change).

### §7 setup_tasks block on /status
- [ ] `GET /tenants/{tid}/status` response includes `setup_tasks` block with `blocker_count`, `warning_count`, `items[]`.
- [ ] Each item carries `id`, `name`, `severity ∈ blocker|warning|info`, `scope ∈ platform|publisher`, `description`, `configure_path`.
- [ ] `house_domain` / `public_agent_url` items render `scope="platform"` for embedded tenants, `scope="publisher"` for open-instance.
- [ ] `default_gam_advertiser_id` (from §1) item renders `severity="blocker"` when null, `scope="publisher"`.
- [ ] Items where the underlying task IS complete render `severity="info"` (Storefront UI choice on whether to display).
- [ ] Status cache invalidation hooks (sprint 1.5: adapter test, PATCH, lifecycle) cover the new block — flipping a tenant's `default_gam_advertiser_id` reflects within 5s.

### §8 auto-syncs + collapsed refresh
- [ ] `Tenant.sync_cadence_minutes` migration runs cleanly; existing tenants get NULL (= use default 360min).
- [ ] `POST /tenants/{tid}/refresh` returns 202 with `sync_run_ids` for inventory/custom_targeting/advertisers.
- [ ] Re-POST within 60s returns the SAME `sync_run_ids` (idempotent — no duplicate jobs queued).
- [ ] `POST /tenants/provision` happy-path response includes `initial_sync.sync_run_ids` after the adapter test passes.
- [ ] Settings page hides per-sync trigger buttons when `tenant.is_embedded=true`; "Refresh tenant" button stays visible.
- [ ] `/status.syncs` block continues to show per-sync health regardless of UI hiding (Storefront renders health from it).
- [ ] cron driver respects `tenant.sync_cadence_minutes` — a tenant with cadence=120 syncs every 2h on the 6h scan, a tenant with cadence=720 only every 12h.

## Resolved questions

1. ~~**`operator_domain` source on `get_products` flows.**~~ **Both endpoints carry it.** `GetProductsRequest.account: AccountReference | None` is real (verified against adcp 4.4.0). Routing chain runs uniformly across both flows; `/recent-buyers` aggregates over `Account` rows that get touched by either. (My initial read was wrong — the field is optional but present.)

2. **`operator_domain` validation: AAO-validated on POST with cache.** Routing-rule POST/PATCH validates the operator publishes a valid adagents.json listing this tenant's `public_agent_url`. Reuses Sprint 1.7's `is_agent_authorized_by_publisher` (6h cache). Read endpoints (GET `/buyer-advertiser-mappings`) stay cheap — no validation on read.

3. **Activation gate: implicit, enforced in `create_media_buy`.** No separate `POST /activate` endpoint, no new state column. The buyer-protocol error path IS the contract: when the routing chain falls through with no default, raise `TENANT_NOT_ACTIVATED`. Storefront's homepage checklist drives off `GET /tenants/{tid}` returning a non-null `default_gam_advertiser_id` (light-up-green client-side, no API ceremony). Sprint 1's `Tenant.is_active` stays the operator-controlled lifecycle field; "activated" config-completeness stays implicit so the two concepts don't collide.

4. **Sandbox interaction: bypass routing rules entirely → per-tenant sandbox advertiser.** Sandbox traffic is ops-test by definition (don't bill, don't pollute reports, don't count against inventory caps); routing it through commercial advertisers defeats the carve-out. Sprint 1.6 already designed `AdapterConfig.gam_sandbox_advertiser_id` (lazy-created on first sandbox call). Sprint 1.8 adds an early-return at the top of `resolve_advertiser_for_buy` when `account_ref.sandbox=true`, with `resolved_via="sandbox"` for the recent-buyers UI. Sprint 1.6's deferred sandbox-advertiser work becomes a prerequisite for Sprint 1.8.

5. **Legacy `Account.resolved_via`: NULL → "unknown".** Migration backfills NULL; recent-buyers surfaces NULL as `"unknown"` in responses. No re-resolve job in this sprint. Optional follow-up tool publishers can run on demand.

## Sprint placement + estimate

**Sprint 1.8** — slots after 1.7 lands. Activation-gate aspect makes it the last sprint required for embedded-mode commercial go-live.

Estimated scope: **~5 days**.
- 0.5d migrations (×5) + Tenant Management API field additions (incl. `default_gam_advertiser_id`, `sync_cadence_minutes`).
- 0.5d `BuyerAdvertiserMapping` schemas + CRUD endpoints + AAO-validate operator_domain on POST.
- 0.5d resolution chain (incl. sandbox early-return) + Sprint 1.6 cleanup (auto_provision_advertisers retirement, `ensure_sandbox_advertiser` helper).
- 0.5d `/gam/advertisers` searchable + paginated.
- 0.5d `/recent-buyers` rollup with resolved_via.
- 0.5d §6 platform-managed lock-down (validators + guard + checklist hide + Settings UI banner).
- 0.5d §7 `setup_tasks` block on /status (severity + scope wiring; fold into status cache).
- 0.75d §8 auto-syncs + `/refresh` endpoint (cadence column + cron branch + provision first-sync + idempotency window + UI button collapse).
- 0.25d preview-adapter advertisers extension (optional).
- 0.5d tests (resolution-chain matrix, CRUD happy paths, dedup constraint, validation errors, guard-rejection, status setup_tasks shape, refresh idempotency).

## Cross-references

- [Sprint 1.6](./sync-accounts-advertiser-mapping.md) — Account + advertiser pre-mapping. The resolved Account row IS the persistent record; this sprint adds the rules that decide *what advertiser to attach* on first creation.
- [Sprint 1.7](./replace-authorized-properties-with-aao-lookup.md) — `Tenant.public_agent_url` (the agent URL publishers list in their adagents.json). Conceptually the "operator side" of routing — the operator_domain in this sprint is the *buyer*'s agent_url.
- [Identity contract](../integration/embedded-mode-identity-contract.md) — these endpoints stay on `X-Tenant-Management-API-Key` auth (publishers manipulate routing config via the host product's UI, not via X-Identity-* iframe sessions).

## Addendum: `auto_provision_advertisers` retained (flag NOT dropped)

**Decision:** the planned `drop_auto_provision_advertisers_from_tenant` migration (item 6 in the Migrations section above, and the "Sprint 1.6 cleanup" line in the estimate) is **cancelled**. The flag stays.

**Why the original retire-the-flag plan was wrong.** Sprint 1.8 conflated two distinct operations:

| Operation | What it does | Replaced by sprint 1.8? |
|---|---|---|
| **Auto-attach** (use a pre-existing advertiser when the buyer has no specific mapping) | Reads existing GAM data | ✅ Yes — `Tenant.default_gam_advertiser_id` + `BuyerAdvertiserMapping` rules cover this case |
| **Auto-create** (`CompanyService.createCompanies` on first buy when no Account exists) | **Writes new state to the publisher's GAM network** | ❌ No — this is a distinct operation that nothing in sprint 1.8 replaces |

Sprint 1.8 replaces the auto-attach role of the flag, but not the auto-create role. Dropping the flag would silently remove a capability with no equivalent on the new path.

**Product policy: explicit opt-in for any GAM-side write.** We don't know what behavior individual host products / publishers will want around advertiser creation. The conservative default — and the right default for an operation that *creates state in someone else's ad server* — is **never auto-create unless explicitly opted in per tenant.** Today's runtime already enforces this: `Tenant.auto_provision_advertisers` defaults `false` (server default in [migration `c8a5e1d3f4b9`](../../alembic/versions/c8a5e1d3f4b9_add_pending_provision_and_auto_provision.py)), and `account_provisioning.py:197` raises `ACCOUNT_NOT_PROVISIONED` whenever the flag is false.

**Outstanding work to make the opt-in usable:**
- The Tenant Management API's `POST /tenants/provision` and `PATCH /tenants/{id}` do not currently accept `auto_provision_advertisers`. A host that *does* want auto-create has no way to set it. Adding it is a small follow-up: one schema field, one PATCH path, one OpenAPI export refresh. Not blocking — the safe default is what it should be.
- The Admin UI doesn't expose the toggle either. Same fix shape; same low priority while we observe what hosts ask for.

**Behavior with the routing chain.** No change to `resolve_advertiser_for_buy`. The chain operates on advertisers that already exist (`default_gam_advertiser_id` and `BuyerAdvertiserMapping` both reference existing GAM advertiser IDs). When neither matches and the buy lands on an unmapped Account in `pending_provision`:

- `auto_provision_advertisers=false` (today's default for every tenant) → raise `ACCOUNT_NOT_PROVISIONED`. Publisher maps manually via the Admin UI / API.
- `auto_provision_advertisers=true` (explicit opt-in per tenant) → call `CompanyService.createCompanies`, persist the new advertiser id, attach the buy.

These paths are complementary, not competing.

**Status of [sync-accounts-advertiser-mapping.md](./sync-accounts-advertiser-mapping.md):** that doc described the original `pending_provision` + `auto_provision_advertisers` flow and predates sprint 1.8's routing chain. The Account + status-precedence machinery it describes is shipped (sprint 1.6 piece A/B/C). The "default true for embedded-mode tenants" framing in that doc is **not** the current product position — see this addendum.
