# Sprint 1.8 Spec: Buyer-Advertiser Routing for Managed Storefronts

**Parent design:** [managed-tenant-mode.md](./managed-tenant-mode.md)
**Builds on:** [sprint 1.6](./sync-accounts-advertiser-mapping.md), [sprint 1.7](./replace-authorized-properties-with-aao-lookup.md)
**Status:** Draft
**Last updated:** 2026-05-04

## Scope

Closes the agent-billed loop on managed-mode storefronts: every buy that lands on a managed publisher gets attributed to the right GAM advertiser based on the buyer's `(operator, brand)` context, with publisher-controlled overrides and a tenant default for the unmatched case.

Three pieces:

1. **`Tenant.default_gam_advertiser_id`** — required-before-activation fallback advertiser. Replaces Sprint 1.6's `auto_provision_advertisers` flag (cleaner: explicit fallback wins over implicit auto-create).

2. **`advertiser_routing_rules` table** — ordered overrides keyed by `(operator_domain, brand_house, brand_id)` with null-as-wildcard. Resolution precedence: exact → house wildcard → operator wildcard → tenant default → reject.

3. **Two read endpoints** — paginated/searchable `/gam/advertisers` (powers UI pickers; large GAM networks have 10k+ companies) + `/recent-buyers` rollup with `resolved_via` per row (lets publishers spot fall-through buyers landing on the default).

Plus an optional preview-adapter extension (§5) so the onboarding flow gets advertisers in the same round trip that confirms the GAM grant.

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

    # 5. No fallback configured → reject.
    raise AdCPError(
        "TENANT_NOT_ACTIVATED",
        message=f"Tenant {tenant_id!r} has no default_gam_advertiser_id and no "
                f"matching routing rule for ({operator}, {brand_house}, {brand_id}).",
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

Source data: `Account` rows for managed-mode tenants — each Account already carries `(operator, brand_house, brand_id)` from sync_accounts upserts AND the resolved `platform_mappings.google_ad_manager.advertiser_id`. We need to add `Account.resolved_via` (one of `"account" | "exact" | "house" | "operator" | "default"`) at first-creation time, and aggregate `request_count` / `last_seen_at` from `MediaBuy` rows joined to Account.

If buyer agents call `get_products` without a follow-up `create_media_buy`, those don't show up here (no Account row gets created on get_products). That's a deliberate gap — the spec says "degrade gracefully if the upstream subset isn't there"; we surface what we have.

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

`src/core/database/managed_tenant_guard.py` (sprint 1) blocks UI-driven writes to platform-managed columns when `tenant.managed_externally=true` AND the session isn't flagged with `info["management_api_caller"]`. Today's protected set on `Tenant` is empty (the comment notes "no publisher-writable platform-managed columns").

Add `house_domain` and `public_agent_url` to the protected set. Result:
- `PATCH /tenants/{tid}` (Tenant Management API, sets `info["management_api_caller"]=True`) → writes succeed.
- Publisher-facing Settings UI POST → write attempt raises `ManagedTenantWriteError`, surfaced as 403 with the existing `managed_tenant_write_blocked` error code.

### Settings page UX

`templates/tenant_settings.html` (or wherever the Settings form lives) gains:

```jinja
{% if tenant.managed_externally %}
<div class="alert alert-info">
  <i class="fas fa-lock"></i>
  These values are platform-managed by {{ tenant.external_source | default('your platform') | title }}.
  Contact your account team to change them.
</div>
{% endif %}

<input type="text"
       name="house_domain"
       value="{{ tenant.house_domain }}"
       {% if tenant.managed_externally %}readonly{% endif %}>
```

The `readonly` attribute is cosmetic (DOM-side only) — the model-layer guard is what enforces. Even if a publisher bypasses the form (e.g. crafted POST), the guard still rejects.

### Setup-checklist hide-when-set

Sprint 1.7 added "Publisher House Domain" + "Public Agent URL" as the first two critical-tasks items. Sprint 1.8 hides them when both are populated AND tenant is managed-externally:

```python
# src/services/setup_checklist_service.py:_check_critical_tasks
if not (tenant.managed_externally and tenant.house_domain and tenant.public_agent_url):
    tasks.append(SetupTask(key="house_domain", ...))
    tasks.append(SetupTask(key="public_agent_url", ...))
```

Open-instance tenants always see both tasks (today's behavior). Managed-externally tenants with both fields set never see them — those decisions live with Scope3, not the publisher. The "Authorized Properties" task already short-circuits to "brand.json reachability probe" when `house_domain` is set (Sprint 1.7), so it's effectively hidden once the platform pre-fills `house_domain`.

### Backfill

No migration needed beyond Sprint 1.7's existing nullable columns. Existing managed-mode tenants get `house_domain` + `public_agent_url` populated by Scope3 via PATCH; legacy open-instance tenants keep NULL and continue to see the setup tasks.

## Migration plan

Three migrations + Sprint 1.6 cleanup:

1. **`add_default_gam_advertiser_id_to_tenant`** — nullable string column. Tenant Management API requires it on managed-mode activation flows.
2. **`create_advertiser_routing_rules_table`** — schema above. Indexed on (tenant_id) and (tenant_id, operator_domain).
3. **`add_resolved_via_to_account`** — nullable enum (`"account" | "exact" | "house" | "operator" | "default"`). Backfill is null for legacy rows; new rows populated by the resolution chain.
4. **`drop_auto_provision_advertisers_from_tenant`** — Sprint 1.6's flag. Replaced by the routing chain. Deferred until impl is verified end-to-end so we have a rollback option.

### Code changes

- **`src/admin/api_schemas/tenant_management.py`** — `BuyerAdvertiserMapping` (request + response variants), `GamAdvertiser`, `RecentBuyer`, `ListBuyerAdvertiserMappingsResponse`, `ListGamAdvertisersResponse`, `ListRecentBuyersResponse`. Plus `default_gam_advertiser_id` on `ProvisionTenantRequest`, `UpdateTenantRequest`, `TenantDetail`. Plus field validators on `house_domain` (domain regex, force-lowercase) and `public_agent_url` (HTTPS-only).
- **`src/admin/tenant_management_api.py`** — five new endpoints (mapping CRUD: GET/POST/PATCH/DELETE; mapping-list GET, advertisers GET, recent-buyers GET).
- **`src/core/database/managed_tenant_guard.py`** — add `house_domain` + `public_agent_url` to the `Tenant` protected-columns set. UI writes from managed-externally tenants raise `ManagedTenantWriteError`.
- **`src/services/buyer_advertiser_routing.py`** — new module: `resolve_advertiser_for_buy(tenant_id, account_ref)` runs the precedence chain.
- **`src/core/tools/media_buy_create.py`** — replace Sprint 1.6's `resolve_account_advertiser` call with the new routing service. Account row creation moves into the resolver.
- **`src/services/gam_advertiser_search.py`** — search/paginate the local `gam_advertisers` cache for `/gam/advertisers`.
- **`src/services/recent_buyers_rollup.py`** — joins Account + MediaBuy for `/recent-buyers`.
- **`src/services/setup_checklist_service.py`** — hide `house_domain` + `public_agent_url` tasks when tenant is managed-externally and both fields are populated.
- **`templates/tenant_settings.html`** — `readonly` on house_domain + public_agent_url inputs when `tenant.managed_externally`; "Platform-managed by Scope3" banner.
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
- [ ] Buy matching exact rule → returns rule's advertiser, `resolved_via="exact"`.
- [ ] Buy matching house wildcard but not exact → `resolved_via="house"`.
- [ ] Buy matching operator wildcard but not house → `resolved_via="operator"`.
- [ ] Buy with no match + tenant has default → `resolved_via="default"`.
- [ ] Buy with no match + tenant has no default → raises `TENANT_NOT_ACTIVATED`.
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
- [ ] `PATCH /tenants/{tid}` (Tenant Management API) updates both fields successfully on a managed-externally tenant.
- [ ] Direct DB write to `Tenant.house_domain` outside the management API on a `managed_externally=true` tenant raises `ManagedTenantWriteError` (model-layer guard).
- [ ] Setup checklist for managed-externally tenant with both fields set OMITS the "Publisher House Domain" + "Public Agent URL" critical-tasks items.
- [ ] Settings page renders `readonly` on both fields + "Platform-managed by Scope3" banner when `tenant.managed_externally=true`.
- [ ] Open-instance (managed_externally=false) tenants always see editable fields + setup tasks (no behavior change).

## Resolved questions

1. ~~**`operator_domain` source on `get_products` flows.**~~ **Both endpoints carry it.** `GetProductsRequest.account: AccountReference | None` is real (verified against adcp 4.4.0). Routing chain runs uniformly across both flows; `/recent-buyers` aggregates over `Account` rows that get touched by either. (My initial read was wrong — the field is optional but present.)

2. **`operator_domain` validation: AAO-validated on POST with cache.** Routing-rule POST/PATCH validates the operator publishes a valid adagents.json listing this tenant's `public_agent_url`. Reuses Sprint 1.7's `is_agent_authorized_by_publisher` (6h cache). Read endpoints (GET `/buyer-advertiser-mappings`) stay cheap — no validation on read.

3. **Activation gate: deferred — open.** Recommended (b) reject `create_media_buy` with `TENANT_NOT_ACTIVATED` when the chain falls through to no default. No separate `POST /activate` endpoint in this sprint. Brian flagged as TBD.

4. **Sandbox interaction: deferred — open.** Recommended sandbox routes through per-tenant sandbox advertiser (Sprint 1.6 § Sandbox) and bypasses these rules entirely. Brian flagged as TBD.

5. **Legacy `Account.resolved_via`: NULL → "unknown".** Migration backfills NULL; recent-buyers surfaces NULL as `"unknown"` in responses. No re-resolve job in this sprint. Optional follow-up tool publishers can run on demand.

## Sprint placement + estimate

**Sprint 1.8** — slots after 1.7 lands. Activation-gate aspect makes it the last sprint required for managed-mode commercial go-live.

Estimated scope: **~3.5 days**.
- 0.5d migrations (×3) + Tenant Management API field additions (incl. `default_gam_advertiser_id`).
- 0.5d `BuyerAdvertiserMapping` schemas + CRUD endpoints + AAO-validate operator_domain on POST.
- 0.5d resolution chain + Sprint 1.6 cleanup (auto_provision_advertisers retirement).
- 0.5d `/gam/advertisers` searchable + paginated.
- 0.5d `/recent-buyers` rollup with resolved_via.
- 0.5d §6 platform-managed lock-down (validators + guard + checklist hide + Settings UI banner).
- 0.25d preview-adapter advertisers extension (optional).
- 0.25d tests (resolution-chain matrix, CRUD happy paths, dedup constraint, validation errors, guard-rejection on managed-externally writes).

## Cross-references

- [Sprint 1.6](./sync-accounts-advertiser-mapping.md) — Account + advertiser pre-mapping. The resolved Account row IS the persistent record; this sprint adds the rules that decide *what advertiser to attach* on first creation.
- [Sprint 1.7](./replace-authorized-properties-with-aao-lookup.md) — `Tenant.public_agent_url` (the agent URL publishers list in their adagents.json). Conceptually the "operator side" of routing — the operator_domain in this sprint is the *buyer*'s agent_url.
- [Identity contract](../integration/managed-mode-identity-contract.md) — these endpoints stay on `X-Tenant-Management-API-Key` auth (publishers manipulate routing config via the upstream platform's UI, not via X-Identity-* iframe sessions).
