# Sync Accounts → GAM Advertiser Mapping

**Status:** Shipped (sprint 1.6 — pieces A, B, C). Resolution semantics partially superseded by [sprint 1.8 routing chain](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md). See [Addendum on `auto_provision_advertisers`](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md#addendum-auto_provision_advertisers-retained-flag-not-dropped).
**Owner:** Sales Agent
**Last updated:** 2026-05-04
**Related:** [embedded-mode-sprint-1.md](./embedded-mode-sprint-1.md), [embedded-mode-sprint-1.8-buyer-advertiser-routing.md](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md), [embedded-mode-sprint-4.md](./embedded-mode-sprint-4.md)

> **Product policy update (post-sprint-1.8).** This doc originally framed `auto_provision_advertisers=true` as the default for embedded-mode tenants. **That is no longer the position.** The default — for every tenant, embedded or open — is `false`: the salesagent does not create advertisers in the publisher's GAM network without explicit per-tenant opt-in. See the [sprint 1.8 addendum](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md#addendum-auto_provision_advertisers-retained-flag-not-dropped) for the full rationale.

## Problem

The salesagent's `sync_accounts` AdCP tool today is pure-internal: it upserts `Account` rows in our DB keyed by `(operator, brand.domain, brand.brand_id, sandbox)` and never touches the seller's ad server. When a buyer agent later calls `create_media_buy` for that account, the impl needs a `gam_advertiser_id` to attach the GAM Order to — but `Account.platform_mappings` is empty.

Today this works for Wonderstruck because there's exactly one advertiser per principal in `Principal.platform_mappings`, hard-coded by the publisher. That's the wrong shape for embedded mode, where a host product will sync hundreds of (operator, brand) pairs and expect them to wire up to real GAM advertisers without manual ops work per pair.

This doc decides:
1. What the natural granularity is — when do two `(operator, brand)` calls produce one advertiser vs. two?
2. When does the salesagent provision a GAM advertiser, and when does it require manual ops?
3. Where do sandbox / dry-run flows fit?

## Granularity decision

The advertiser is the GAM `Company(type='ADVERTISER')` row that invoices appear under. The salesagent `Account` should be 1:1 with that billing entity.

| `Account.billing` | Natural key | Why |
|---|---|---|
| `operator` (default today) | `(operator, brand.domain, brand.brand_id, sandbox)` | Operator is invoiced. Different buyer agents pulling the same brand share one GAM advertiser — they're all selling into the same operator/brand relationship and the operator's books need one row per brand they're paid for. |
| `agent` | `(buyer_agent_principal_id, operator, brand.domain, brand.brand_id, sandbox)` | The buyer agent is invoiced. The buyer agent is the calling principal — `identity.principal_id` from the request's auth chain. Different buyer agents on the same `(operator, brand)` are different commercial relationships — different rate cards, commissions, audit trails. They MUST be different GAM advertisers so finance can split the books. |
| any with `sandbox=true` | route to a single per-tenant `__sandbox__` advertiser | Sandbox traffic must never bill, never appear in production reports, never count against publisher inventory caps. Keep all sandbox media buys against one synthetic advertiser the salesagent owns. |

**`account_scope` already encodes this** in our schema (`operator | brand | operator_brand | agent` — `models.py:875`). The natural key above maps onto it: agent-billed → `agent` scope, operator-billed → `operator_brand` scope, sandbox → ignore scope, route to sandbox bucket.

**Note on `governance_agents`:** that AdCP field is unrelated — it lists agents with audit/oversight authority over the Account (delegation), not the buyer agent in the billing relationship. The buyer agent for `billing=agent` is unambiguously the calling principal; there's no fallback question to resolve.

## Lifecycle

`sync_accounts` and `create_media_buy` split responsibilities. Sync records intent; first-buy provisions GAM.

### `sync_accounts` — record intent only

This stays a pure salesagent-internal upsert. Behavior unchanged from today's impl, with two additions:

1. **New status:** `pending_provision` joins the existing `(active | pending_approval | rejected | payment_required | suspended | closed)` enum (`models.py:863`). Means "we have an Account row, no GAM advertiser yet, waiting for a media buy or manual provision."

   New accounts created via sync land in:
   - `pending_approval` if tenant `account_approval_mode != 'auto'` (today's BR-RULE-060 path).
   - `pending_provision` if approval is auto AND tenant has GAM AND `Account.platform_mappings.gam_advertiser_id` is unset.
   - `active` only when there's a confirmed GAM advertiser id wired up.
   - Sandbox accounts: always `active` and immediately wired to the per-tenant sandbox advertiser (no provisioning step).

2. **Setup hint when in `pending_provision`:** the `setup` block on the response gets a `provision_url` pointing at an Admin UI page where the publisher can manually attach a GAM advertiser id (existing GAM advertiser picker — already in the Admin UI). For tenants with `auto_provision_advertisers=true`, the setup block can omit the URL since first-buy will trigger the create automatically.

`sync_accounts` STILL never calls GAM. This keeps GAM-advertiser cardinality bounded by actual buying activity — buyers exploring "do you support brand X?" via sync don't burn GAM rows.

### `create_media_buy` — provision-on-first-buy

When `_create_media_buy_impl` resolves an Account in `pending_provision`:

```python
account = repo.get(media_buy_request.account_id)
if account.status == "pending_provision":
    if account.sandbox:
        advertiser_id = ensure_sandbox_advertiser(tenant_id)  # cached per tenant
    elif tenant.auto_provision_advertisers:
        advertiser_id = gam.create_advertiser(name=account.name)  # CompanyService.createCompanies
        repo.attach_advertiser(account.account_id, advertiser_id)
        account.status = "active"
    else:
        raise AdCPError(
            "ACCOUNT_NOT_PROVISIONED",
            message=f"Account {account.account_id} has no GAM advertiser. "
                    "Publisher must map manually via Admin UI before this account "
                    "can buy media.",
            recovery="terminal",  # buyer can't resolve; ops must
        )
```

The `ACCOUNT_NOT_PROVISIONED` path is the **default for every tenant** — the salesagent files an approval workflow visible in the Admin UI; the publisher (or the host product driving the API) maps the advertiser explicitly. The `auto_provision_advertisers=true` path is for tenants who have explicitly delegated advertiser-create authority to the salesagent. There is no implicit default-to-true for embedded mode (or any other mode); the policy is "explicit opt-in only" because auto-create writes new state into the publisher's GAM network.

### `Tenant.auto_provision_advertisers`

New boolean column on `Tenant`. Default `false` for **every** tenant — embedded or open. Auto-creating advertisers writes state into the publisher's GAM network, and we don't presume to do that without per-tenant explicit consent.

The Tenant Management API's `POST /tenants/provision` and `PATCH /tenants/{id}` should accept this as an optional field (not currently wired — see [sprint 1.8 addendum](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md#addendum-auto_provision_advertisers-retained-flag-not-dropped)). When wired, the API default remains `false`; a host product opts in per tenant by passing `auto_provision_advertisers: true` in the request. Open-instance Admin UI exposes it as a config toggle on the GAM adapter page.

Migration: `add_auto_provision_advertisers_to_tenant` adds the column with `server_default='false'`.

## Storage

Use `Account.platform_mappings` (existing column, currently unused — `models.py:850`):

```json
{
  "google_ad_manager": {
    "advertiser_id": "1234567890",
    "advertiser_name": "Acme News × Coca-Cola (Scope3 BuyerAgent)",
    "provisioned_at": "2026-05-04T17:00:00Z",
    "provisioned_by": "auto" | "manual:user@operator.com"
  }
}
```

Don't reuse `Principal.platform_mappings` (line 559) for this. Principal mappings are agent-level credentials (e.g., the Wonderstruck single-advertiser today's hack); Account mappings are billing-entity-level. Mixing them collapses the granularity decision back to one-advertiser-per-agent.

The advertiser-name template is the only scaling concern: GAM enforces uniqueness on company names. Template should embed the agent + operator + brand to avoid collisions:

```
"{operator} × {brand_domain} ({agent_name})"  if billing=agent
"{operator} × {brand_domain}"                  if billing=operator
"{tenant_name} Sandbox"                        if sandbox
```

If creation fails with a name conflict (existing advertiser with the same template), the salesagent fetches the existing one's id and attaches it to the Account — same logical advertiser, no duplication.

## Sandbox

Sandbox accounts (`Account.sandbox=true`) NEVER get a real GAM advertiser. Behavior:

1. On first sync, salesagent ensures a per-tenant sandbox advertiser exists in GAM (created lazily on first sandbox account, reused thereafter). Stored on `AdapterConfig.gam_sandbox_advertiser_id`.
2. Every sandbox `Account.platform_mappings.advertiser_id` points at this single sandbox advertiser.
3. Sandbox media buys go to GAM but use `dry_run=true` (or whatever the GAM adapter calls it) so they create real Order rows but never serve. This keeps the buyer's storyboard exercising real GAM machinery without polluting production reports.
4. **Open question 2:** confirm GAM's dry-run semantics actually create rows that show in the publisher's UI but don't serve, vs. rejecting at the API layer. If they reject, sandbox needs a separate "test GAM network" entirely — out of scope for v1.

## Pre-mapping (Tenant Management API)

Publishers — and host products driving them programmatically — need a way to wire GAM advertisers to billing keys *before* any buyer agent calls `sync_accounts`. Otherwise every first-buy on a new brand burns an `ACCOUNT_NOT_PROVISIONED` round trip even when the publisher already knows which advertiser belongs where.

The mapping IS the Account. We expose Account upsert through the Tenant Management API; pre-mapping is just creating Accounts ahead of time with `platform_mappings.gam_advertiser_id` pre-attached and `status=active`.

### `POST /api/v1/tenant-management/tenants/{tid}/accounts`

Upsert by the same natural key `_sync_accounts_impl` uses, with the GAM advertiser pre-attached:

```python
class CreateAccountRequest(BaseModel):
    name: str | None = None        # display name; auto-generated if omitted
    operator: str                  # required
    brand: BrandReference          # {domain, brand_id?}
    billing: Literal["operator", "agent"]
    buyer_agent_principal_id: str | None = None  # required iff billing=agent
    sandbox: bool = False
    gam_advertiser_id: str         # the whole point of this endpoint
    gam_advertiser_name: str | None = None  # optional cache for display
    payment_terms: str | None = None
    rate_card: str | None = None
```

Behavior:
1. Validate request — `billing=agent` requires `buyer_agent_principal_id`; `sandbox=true` rejects `gam_advertiser_id` (sandbox accounts route to the per-tenant sandbox advertiser, not a caller-specified one).
2. Look up by natural key. For `billing=operator|sandbox`: `(operator, brand_domain, brand_id, sandbox)`. For `billing=agent`: that key + `principal_id`.
3. **Upsert:** existing → update `platform_mappings.gam_advertiser_id`, flip `status=active` if it was `pending_provision`, return 200 with the updated Account. Missing → create with status=`active`, return 201.
4. Return the full `AccountDetail` either way.

When `sync_accounts` later comes in for the same natural key, the existing upsert finds the row, updates fields if anything drifted, leaves the advertiser id intact, and returns `unchanged` or `updated`. No `pending_provision` round trip.

### `GET /api/v1/tenant-management/tenants/{tid}/accounts`

List accounts for a tenant. Optional filters:
- `?operator=` — exact match
- `?billing=operator|agent` — filter by billing model
- `?advertiser_mapped=true|false` — has `platform_mappings.gam_advertiser_id` set?
- `?status=active|pending_provision|...`
- `?sandbox=true|false`

Returns `ListAccountsResponse` with `accounts: list[AccountSummary]` and a `count`.

### Why expose this and not `PATCH` / `DELETE` initially

POST handles upsert (create or remap an existing Account's advertiser id by re-POSTing). GET answers "what's the current state?" — these two cover the Storefront-driven workflow (push mappings, verify, re-push deltas). Explicit `PATCH /accounts/{id}` and `DELETE` (soft-close to `status=closed`) can land if the cardinality of repeated POST upserts becomes an audit-log nuisance, but they're not on the critical path.

### Tradeoff: exact-match vs. wildcards

This design requires one Account row per natural-key combination — no wildcards like "any agent on AccuWeather × cocacola.com → advertiser 12345." For `billing=operator` that's fine (one row per operator/brand). For `billing=agent` it's potentially N×M (every agent × every brand). Defer the wildcard question until a real deployment hits cardinality pain — then a separate `account_advertiser_rules` table can express patterns and `_sync_accounts_impl` consults it as a fallback when no exact-match Account exists.

## Migration plan

Three salesagent migrations + one schema bump:

1. `add_auto_provision_advertisers_to_tenant` — `Tenant.auto_provision_advertisers` boolean, default false. Migration only; no code path reads it yet.
2. `add_pending_provision_to_account_status` — extend the CHECK constraint on `Account.status` to include `pending_provision`. Existing rows unaffected.
3. `add_gam_sandbox_advertiser_id_to_adapter_config` — `AdapterConfig.gam_sandbox_advertiser_id` nullable string. Lazily populated.

Code changes:

1. `sync_accounts` impl — set new accounts to `pending_provision` when GAM is configured and no manual mapping exists. Sandbox accounts get the sandbox advertiser immediately.
2. `create_media_buy` impl — branch on Account status; provision-on-first-buy when `auto_provision_advertisers=true`; raise `ACCOUNT_NOT_PROVISIONED` otherwise.
3. `GAMOrdersManager.create_advertiser(name) -> str` — new helper. Calls `companyService.createCompanies([{name, type: 'ADVERTISER'}])`, returns the new id. Handles name-collision-as-attach (look up existing, return its id).
4. Admin UI — add a "Map advertiser" button on Account rows in `pending_provision` status. Calls a new endpoint `POST /admin/accounts/{id}/attach-advertiser` that takes a GAM advertiser id and attaches it.

## Acceptance criteria

- [ ] `sync_accounts` with billing=operator → creates Account in `pending_provision` (or `pending_approval` per existing BR-RULE-060) with no GAM call.
- [ ] `sync_accounts` with billing=agent → same, but natural key includes the calling agent's id; two agents syncing the same `(operator, brand)` produce two distinct Accounts.
- [ ] `sync_accounts` with sandbox=true → Account immediately `active` and wired to per-tenant sandbox advertiser. No GAM CompanyService.createCompanies call (advertiser was created lazily on first sandbox call ever, reused thereafter).
- [ ] `create_media_buy` for `pending_provision` Account on tenant with `auto_provision_advertisers=true` → calls `GAMOrdersManager.create_advertiser`, persists id on `Account.platform_mappings.google_ad_manager.advertiser_id`, flips Account to `active`, proceeds with the buy.
- [ ] `create_media_buy` for `pending_provision` Account on tenant with `auto_provision_advertisers=false` → raises `ACCOUNT_NOT_PROVISIONED`. Admin UI shows the account with a "Map advertiser" prompt.
- [ ] Two media buys against the same `pending_provision` Account in quick succession (race) → only one GAM CompanyService.createCompanies call (idempotency on the provision step keyed by `account_id`).
- [ ] Advertiser-name collision in GAM → existing advertiser id is attached, no error. Audit log records the attach.
- [ ] Tenant Management API `POST /tenants/provision` and `PATCH /tenants/{id}` accept `auto_provision_advertisers` as an optional field; default `false` for **every** tenant (embedded or open). Per the [sprint 1.8 addendum](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md#addendum-auto_provision_advertisers-retained-flag-not-dropped), there is no implicit default-to-true for embedded mode — auto-creating advertisers writes state into the publisher's GAM network and requires explicit consent.
- [ ] Existing open-instance tenants unaffected after migration — `auto_provision_advertisers=false` by default keeps today's manual-mapping flow intact.

## Open questions

1. **GAM dry-run semantics** — does `dry_run=true` on order create produce visible-but-non-serving Orders, or reject at the API? Affects whether sandbox shares the production GAM network or needs its own.
2. **Cross-agent advertiser sharing on operator-billed accounts** — when two buyer agents sync the same `(operator, brand_domain, brand_id)` with `billing=operator`, do they end up sharing one Account row (current natural key) or get separate `AgentAccountAccess` rows pointing at one Account? Today it's the latter; confirm that's still right for embedded mode.
3. **Provisioning idempotency** — what happens if the GAM `CompanyService.createCompanies` call succeeds but the salesagent's commit-to-DB fails? On retry we'd duplicate the advertiser. Mitigation: `create_advertiser` always queries existing-by-name first, OR persist intent before the GAM call (`Account.platform_mappings.advertiser_create_pending=true`) and reconcile on retry.
4. **Manual mapping UX** — the Admin UI's existing GAM advertiser picker shows all advertisers in the network. For large publishers (10k+ advertisers), it needs search. Out of scope here; flag for the Admin UI sprint.

## Sprint placement

This work fits in **Sprint 1.6 or Sprint 4** of the embedded-mode plan. Lighter than Sprint 4's full publisher-CRUD scope; depends on Sprint 1's Tenant Management API existing (which it does). Recommend landing as a discrete sprint right after 1.5 since the first embedded-mode `create_media_buy` will hit `ACCOUNT_NOT_PROVISIONED` without it.

Estimated scope: ~3 days.
- 0.5d migrations + Tenant flag.
- 1d sync_accounts + create_media_buy branching.
- 0.5d GAM `create_advertiser` helper + sandbox-advertiser bootstrap.
- 0.5d Admin UI "Map advertiser" button + endpoint.
- 0.5d tests (provision happy path, manual-required path, sandbox carve-out, race idempotency, name-collision-as-attach).
