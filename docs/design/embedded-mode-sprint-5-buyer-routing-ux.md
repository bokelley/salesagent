# Sprint 5 Spec: Buyer Routing UX overhaul + GAM advertisers cache

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** Sprint 1.8 (routing rules + default advertiser),
Sprint 4 (UI hardening)
**Status:** Captured (full overhaul + deployment plan)
**Last updated:** 2026-05-04

## Why a full overhaul

Today's `Settings → Advertisers` page is misleadingly named (it CRUDs
`Principal` rows — buyer-protocol identities — not GAM companies) and
has zero visibility into:

- What `(operator, brand)` triples are actually arriving at this tenant
- Which buys are routing to the default advertiser vs a specific rule
- Which GAM advertisers exist in the publisher's network and which
  are mapped vs unmapped
- Sandbox flow, end-to-end

The data is all there (Sprint 1.8 routing rules, `Account.resolved_via`,
`/recent-buyers`); the UI never caught up. Every PSA tenant — embedded
hosts, standalone publishers — needs this surface. Building it once in
PSA serves all adopters; building it per-host (e.g. inside Scope3
Storefront) duplicates work and fragments the canonical source of truth.

Embedded simplification accepted: in embedded mode, the principal is
always the host (e.g. Scope3 / Interchange). Per-principal mapping
nuance doesn't exist for embedded — there's exactly one buyer agent,
identified by header. The UI design focuses on `(operator, brand) →
GAM advertiser` as the user-facing mental model; Principal stays a
hidden technical detail.

## UX target

One page: **`/tenant/<id>/buyer-routing`** (or "Advertiser Mapping" —
naming TBD; the underlying URL is what matters). Replaces today's
Settings → Advertisers as the canonical mapping surface. The current
Settings → Advertisers page becomes a hidden technical detail,
accessible only via direct URL for ops debugging.

### Three sections, one page

#### 1. Default advertiser

```
╭─────────────────────────────────────────────────────────────╮
│  Default GAM advertiser: [Scope3-Interchange-1 (12345)] ▼  │
│  All unmapped buys land here. Required for activation.      │
╰─────────────────────────────────────────────────────────────╯
```

- Single picker bound to `Tenant.default_gam_advertiser_id`
- Picker is the same searchable component as the routing-rule editor
  (uses `GET /gam/advertisers` — Piece D)
- `null` shows a `⚠️ Tenant not activated` banner with copy explaining
  that `TENANT_NOT_ACTIVATED` is the buyer-protocol error today and
  picking a default is what gates first commercial buy
- Save → `PATCH /tenants/{id} { default_gam_advertiser_id: ... }`

#### 2. Routing rules

```
Routing rules                                       [+ Add rule]
┌──────────────┬─────────────┬───────────┬─────────────────────┐
│ Operator     │ Brand house │ Brand id  │ GAM advertiser      │
├──────────────┼─────────────┼───────────┼─────────────────────┤
│ scope3.com   │ wpp.com     │ —         │ Scope3-WPP (67890)  │
│ scope3.com   │ publicis.com│ —         │ Scope3-Publicis     │
│ buyer.x.com  │ —           │ —         │ Acme-Direct         │  ← operator wildcard
└──────────────┴─────────────┴───────────┴─────────────────────┘
```

- Lists all rows from `advertiser_routing_rules` (via `GET /buyer-advertiser-mappings`)
- Sorted by precedence (exact > house wildcard > operator wildcard)
- Empty cells render as `—` for nulls; tooltip explains wildcard
  semantics ("matches any brand under this house")
- Add: modal with operator_domain (free-text or autocomplete from
  recent-buyers history), brand_house (optional), brand_id
  (optional, requires brand_house), GAM advertiser picker (Piece D)
- Edit: same modal; operator_domain disabled (DELETE+POST-only per
  current API contract — matches what Sprint 1.8 §6 already enforces)
- Delete: confirm dialog → `DELETE /buyer-advertiser-mappings/{id}`
- Inline 409 handling: if the natural key collides with an existing
  rule, surface "another rule already maps this triple" with a link
  to the conflicting row

#### 3. Recent activity

```
Recent activity (last 30 days)              [filter: all] ▼
┌──────────────┬─────────────┬─────────┬──────┬──────────────┬─────────┐
│ Operator     │ Brand       │ Buys    │ Last │ Advertiser   │ Route   │
├──────────────┼─────────────┼─────────┼──────┼──────────────┼─────────┤
│ scope3.com   │ wpp/coke    │ 142     │ 2h   │ Scope3-WPP   │ exact   │ ← green
│ scope3.com   │ wpp/sprite  │ 38      │ 3h   │ Scope3-WPP   │ house   │ ← blue
│ scope3.com   │ pmi.com     │ 12      │ 1d   │ Scope3-Inter │ default │ ← amber
│ buyer.y.com  │ random.com  │ 4       │ 5d   │ Scope3-Inter │ default │ ← amber
└──────────────┴─────────────┴─────────┴──────┴──────────────┴─────────┘
                                                       [Promote ↑]
```

- Source: `GET /recent-buyers?days=30` (already shipped)
- Color-coding by `resolved_via`:
  - **green** — `exact` (full natural-key match)
  - **blue** — `house` (brand_house wildcard match)
  - **teal** — `operator` (operator wildcard match)
  - **amber** — `default` (fall-through to tenant default)
  - **purple** — `account` (existing Account row — pre-mapped via
    Sprint 1.6 `/accounts` API)
  - **grey** — `unknown` (legacy NULL row, predates Sprint 1.8)
  - **slate** — `sandbox` (carve-out)
- Filter dropdown: all / matched (green/blue/teal) / unmatched
  (amber) — publishers see the fall-throughs first
- "Promote to rule" action on any row → opens Add Rule modal
  prefilled with that triple → publisher picks GAM advertiser, saves
- Sortable by Buys desc / Last seen desc / Operator A-Z
- Pagination: `?days` + `?limit` query params (already supported)

#### 4. Sandbox section (collapsed by default)

```
▶ Sandbox accounts (3)
  Auto-routed to per-tenant sandbox advertiser. Don't bill, don't
  count against inventory. Useful for buyer-side dry runs.
```

- Lists Accounts where `sandbox=True`
- Read-only; sandbox routing is internal infrastructure
- Click expands to show table with same columns as Activity but no
  promote action

### Navigation

Adds a new top-level tenant nav entry: **Buyer Routing** (between
Advertisers — soon hidden — and Inventory). Visible on all tenant
types (standalone publishers + embedded hosts both need this).

The current Settings → Advertisers tab gets demoted: in embedded mode
it stays hidden; in open-instance mode it remains accessible for
historical reasons (debugging Principal rows directly) but the in-app
docs / setup checklist points new users to Buyer Routing instead.

## Piece D: GAM advertisers cache

The picker in §1 + §2 above needs a searchable list of the publisher's
GAM advertisers. Sprint 1.8 deferred this; it lands here.

### Schema

```python
class GamAdvertiser(Base):
    __tablename__ = "gam_advertisers"
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    advertiser_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_gam_advertisers_tenant", "tenant_id"),
        Index("idx_gam_advertisers_name", "tenant_id", "name"),  # case-insensitive search
    )
```

### Sync

`SyncJob.sync_type = "advertisers"` already exists in the
`/refresh` endpoint's fan-out (Sprint 1.8 §8) but the worker-side
implementation is empty. Implement it:

- Read GAM `CompanyService.getCompaniesByStatement WHERE type = 'ADVERTISER'`
- Upsert into `gam_advertisers` (keyed on tenant_id + advertiser_id)
- Soft-delete (status='inactive') for advertisers that disappeared
  from GAM since last sync — don't hard-delete, routing rules might
  reference them
- Run on `/refresh` POST (already wired) + cron schedule (Sprint 1.8
  §8 cron rework — separately deferred)
- Bulk-page through GAM (10k+ advertisers per network is realistic)

### Endpoint

```
GET /api/v1/tenant-management/tenants/{id}/gam/advertisers
    ?q=<substring>     # case-insensitive name match OR exact id match
    &limit=<int>       # default 50, max 500
    &cursor=<opaque>   # base64-encoded offset for pagination

→ {
    "advertisers": [
      { "id": "12345", "name": "Acme Sports", "currency_code": "USD", "status": "active" },
      ...
    ],
    "next_cursor": "eyJvZmZzZXQiOjUwfQ==" | null,
    "synced_at": "2026-05-04T12:00:00Z"
  }
```

- Reads from local `gam_advertisers` cache (NOT live GAM — must be
  cheap, ~10k networks)
- `q` < 2 chars → return first page unfiltered (avoid expensive
  full-text scan from typing the first character)
- Numeric `q` → exact id match only (single-result return)
- Cursor format: opaque base64 of `{"offset": N}` — same pattern as
  existing pagination utilities

### Where it's consumed

- The Buyer Routing page picker (default advertiser + routing-rule
  rows)
- `POST /buyer-advertiser-mappings` validation (the API now rejects
  mappings to non-existent advertisers — Sprint 1.8's deferred
  validation lands here)
- Optional: Settings → adapter-config preview (one round trip during
  onboarding gives publisher both connection-test + advertiser list)

## Parallelization

Five workstreams. Dependency graph:

```
A. gam_advertisers cache (table + sync + endpoint)   ─┐
                                                       ├─► C. Editor (default + rule CRUD)  ─┐
B. UI scaffolding (new page, nav, read-only)         ─┤                                       ├─► E. Promote-to-rule
                                                       └─► D. Activity view                  ─┘
```

**Workstream A — gam_advertisers cache (~1.5 days)**
Backend-only. Migration, model, sync_advertisers worker function,
`GET /gam/advertisers` endpoint, tests. Depends on nothing else.

**Workstream B — UI scaffolding (~0.5 days)**
New blueprint route `/tenant/<id>/buyer-routing`, new template
`buyer_routing.html` with the three-section skeleton, nav entry,
all read-only initially (just renders existing data via existing
endpoints). Depends on nothing — uses already-shipped APIs.

**Workstream C — Editor: default + rule CRUD (~1 day)**
Wires the picker, modals, save handlers. Depends on A (advertiser
picker needs the cache) and B (skeleton).

**Workstream D — Activity view (~0.5 days)**
Renders `/recent-buyers` as a sortable table with `resolved_via`
color-coding + filter dropdown. Pagination wired to `?days` /
`?limit`. Depends on B (skeleton); fully parallel with C.

**Workstream E — Single-click promote (~0.5 days)**
Adds the promote action on Activity rows that opens the rule editor
prefilled. Depends on C (editor exists) + D (rows clickable).

**Total scope: ~4 days** if parallelized; ~5 days serial.

A and B can start in parallel today. C waits for A; D can start with
B and run in parallel with C. E is the final convergence.

## Deployment strategy

### Today's state
- **Fly.io**: live tenants (Wonderstruck at minimum) running the
  current `prebid/salesagent` upstream + our embedded-mode patches
- **Frontend**: Storefront on GCP
- **Migrations shipped this session that affect existing data:**
  - `e7a4c2b9d5f1` — Sprint 1.8 (additive: new tables + columns,
    safe to apply)
  - `c4d5e6f7a8b9` — `managed_externally → is_embedded` (column
    rename — old code reading `managed_externally` BREAKS after
    this migration runs)
  - `d5e6f7a8b9c0` — `embed_breadcrumb_root` (additive)
  - Sprint 5 will add: `gam_advertisers` table (additive)

### The hard constraint
The `managed_externally → is_embedded` rename means **the old
codebase cannot share a database with the new codebase**. Old code
issues `SELECT managed_externally FROM tenants` and gets a column-
not-found error. This rules out:
- Running new + old apps against the same Fly database
- A/B testing two versions on shared state

### Five real options

**Option 1: In-place upgrade on Fly**
- Push to current Fly app, migrations run on deploy
- All existing tenants (Wonderstruck) get the new code
- Pro: zero infra work; existing data preserved
- Con: hard rollback (column rename is reversible per the migration's
  `downgrade()` but app code rolls forward only); single coordinated
  cutover for all tenants
- Risk: Wonderstruck is mid-flight on the existing surface; any
  embedded-mode-related regression we missed lands on a real customer

**Option 2: New Fly app + new database**
- Spin up `salesagent-v2.fly.dev` + new Postgres
- Migrate Wonderstruck via `pg_dump | pg_restore` + run alembic head
- Pro: clean slate; rollback = point DNS back to old app
- Con: need data migration; planned downtime for Wonderstruck (small
  — minutes, not hours, given data volume)
- Con: still on Fly, not co-located with frontend

**Option 3: New Fly app + same database** ❌ rejected
- Old app + new app both writing — first migration breaks the old
- Not viable given the rename

**Option 4: Fresh GCP deployment + same Fly database via cross-cloud DB connection** ❌ not recommended
- Latency cost (Fly DB → GCP app) is real (~50-100ms per query
  depending on regions)
- Doesn't solve eventual migration off Fly anyway
- Cross-cloud DB connections are operationally fragile

**Option 5: Fresh GCP deployment + GCP Cloud SQL, migrate Wonderstruck** ✅ recommended
- New PSA on GCP Cloud Run (or GKE) + Cloud SQL Postgres
- Frontend (Storefront, GCP) and backend now co-located → no cross-
  cloud latency, simpler ops
- Wonderstruck migration: `pg_dump` Fly → `pg_restore` Cloud SQL +
  run `alembic upgrade head`. Minutes of downtime if coordinated;
  the new alembic chain is additive on top of the existing schema
- Decommission Fly deploy after cutover

### Fork question

Two pressures pull in different directions:
- **Stay on upstream `prebid/salesagent`**: free upstream contributions
  flowing in; everyone benefits from upstream fixes; embed-mode work
  becomes the canonical multi-tenant story for the project
- **Fork to `agenticadvertising/salesagent` (or similar)**: control
  release timing; can ship ahead of upstream review; long-running PRs
  don't block production

**Recommended hybrid:**

1. **Fork now** for deployment control. Branch `embedded-mode-v1` on
   the fork; deploy from there.
2. **Open the same diffs as upstream PRs** sized to be reviewable
   (Sprint 1.8 as one PR, Sprint 4 as another, Sprint 5 as another;
   the rename PR carries the wire-shape compatibility shim so
   upstream merges don't break their existing adopters).
3. When upstream merges, rebase the fork's `embedded-mode-v1` onto
   upstream `main` to stay aligned. If they reject or stall: keep
   shipping from the fork.
4. After ~6-12 months of stability, propose the fork as PSA 2.0 to
   upstream — at that point the embedded-mode work is battle-tested,
   the design is concrete, and the upstream merge is "accept the
   2.0 line" rather than "review 60 PRs."

Cost of the fork: keeping a `merge-from-upstream` cron and the
discipline to actually run it. Cheap relative to the alternative
(blocking on upstream review for production rollouts).

## Test coverage gap

What's been touched in embedded-mode work this session:

| Surface | Status |
|---|---|
| Auth (X-Identity-* bypass) | ✅ tested + live-verified |
| Tenant lifecycle (provision / patch / lifecycle) | ✅ |
| Status block (sprint 1.5 + setup_tasks + products) | ✅ |
| Routing rules CRUD | ✅ |
| Routing chain + cutover (TENANT_NOT_ACTIVATED) | ✅ + live-verified |
| Recent-buyers | ✅ |
| Refresh endpoint | ✅ |
| Setup tasks block on /status | ✅ |
| UI hardening (HTML response level) | ✅ |
| Embed breadcrumbs | ✅ |
| Embedded-mode rename + adcp 4.4.0 pin | ✅ |
| Architecture-guard catch-up (raw-select allowlist) | ✅ |

**What HASN'T been exercised under embedded mode (the real gap):**

| Surface | Concern |
|---|---|
| `_create_media_buy_impl` end-to-end against Mock + GAM | Exercised in unit tests; not yet driven via X-Identity-* auth bypass against running stack |
| `_update_media_buy_impl` (pause / resume / budget changes) | Untested under embedded auth |
| `_sync_creatives_impl` + `_list_creatives_impl` | Same — auth-bypass path with creative payloads not driven |
| `_get_media_buy_delivery_impl` (reporting) | Not driven via embedded auth |
| `_get_signals_impl` | Not driven |
| Webhooks (outbound delivery webhook delivery) | Webhook URLs are set per-tenant; embedded tenants haven't been exercised end-to-end |
| Workflow steps + HITL approvals | The approval flow under X-Identity-* identity is not exercised |
| Strategy CRUD | Untouched in embedded context |
| Product CRUD via API (not just /status counter) | Untouched |
| `cancel_media_buy` | Untested under embedded auth |

The pattern: anything that's `_impl` business logic was tested in
isolation; the path **transport boundary → identity resolution →
business logic** under embedded mode was only exercised end-to-end
for the surfaces directly touched (provision, status, routing, refresh).

### Recommended pre-deploy verification

Extend `scripts/verify_sprint_1_8.py` (the live verification harness)
into a broader `scripts/verify_embedded_mode.py` that drives the full
buyer-protocol surface against a managed-mode tenant via X-Identity-*
headers:

1. Provision tenant + default advertiser + first routing rule
2. `get_products` with inline AccountReference
3. `create_media_buy` happy path + auto-account-creation cutover
4. `sync_creatives` with creative payload
5. `get_media_buy_delivery` (mock impressions injected)
6. `update_media_buy` (pause + resume)
7. `cancel_media_buy`
8. Webhook delivery (assert outbound POST was made)
9. HITL workflow step (if applicable)

Each row asserts behavior + writes to JSON. Becomes the
pre-deployment gate — green script = ship, any red = fix-and-retry.

This is ~1 day of script work. Recommended **before** GCP deploy,
because finding regressions under live customer load is ten times
more expensive than finding them in a verification harness.

## Acceptance criteria

### Sprint 5 deliverables
- [ ] `gam_advertisers` table migration runs cleanly
- [ ] `sync_advertisers` worker pulls + upserts from GAM
- [ ] `GET /gam/advertisers` endpoint returns paginated, searchable
      results from local cache; sub-100ms response on 10k-row networks
- [ ] `POST /buyer-advertiser-mappings` validates against the cache
      (rejects unknown gam_advertiser_id with 400)
- [ ] `/tenant/<id>/buyer-routing` page renders with all three sections
- [ ] Default advertiser picker bound to `Tenant.default_gam_advertiser_id`
- [ ] Routing rule CRUD round-trips via the existing endpoints
- [ ] Activity view color-codes by `resolved_via`; filter by matched/unmatched works
- [ ] "Promote to rule" on an Activity row opens the editor prefilled
- [ ] Sandbox section collapsed-by-default, expandable
- [ ] Top-level "Buyer Routing" nav entry visible on all tenant types
- [ ] Settings → Advertisers tab demoted (hidden in embedded; deprecated note in open-instance)
- [ ] Existing 262+ tests still pass; ~30 new tests for Sprint 5 surfaces

### Pre-deployment gate
- [ ] `scripts/verify_embedded_mode.py` covers the 9 buyer-protocol
      flows above with all-pass green
- [ ] Wonderstruck migration dry-run on a Cloud SQL replica succeeds
- [ ] Storefront integration test (iframe → embed-breadcrumb-root +
      X-Identity-* + tenant-management API) passes end-to-end against
      the new GCP deployment

## Open questions

1. **Page name** — "Buyer Routing" vs "Advertiser Mapping" vs
   "Routing Rules". My pick: "Buyer Routing" — clearest about what
   the page does ("decide which buyer goes where"). User input?

2. **Embedded hosts and the rules editor** — host UI may want to
   embed the rules editor INTO their own dashboard rather than
   deep-link into PSA's. Easy follow-up: add a stripped-down
   `?embedded=1` mode that drops chrome (already a pattern for the
   admin shell) so it iframes cleanly. Defer this until host asks.

3. **Cron rework for sync_cadence_minutes** — Sprint 1.8 §8 left this
   open. It should land before Sprint 5 if we want fresh
   `gam_advertisers` data without manual `/refresh` clicks. Add to
   the parallelization plan as workstream A' (cron updates), depends
   on A but parallel with B/C/D/E.

4. **Wonderstruck data on Fly** — what's the actual customer status?
   Is Wonderstruck actively transacting or in dev/test mode? Decides
   how much downtime tolerance the migration cutover gets.

## Sprint placement + estimate

**Sprint 5** — slots after Sprint 4's UI hardening lands.

Estimated scope (parallelized): **~4 days** of implementation +
**~1 day** of pre-deploy verification harness + **~0.5 day**
deployment cutover.

Total wall-clock: ~1 week if two engineers work in parallel; ~2
weeks serial.

## Cross-references

- [Sprint 1.8](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md)
  — routing rules + default advertiser. This sprint surfaces those
  in the UI.
- [Sprint 4 UI hardening](./embedded-mode-sprint-4-ui-hardening.md)
  — what's hidden from publishers in embedded mode.
  Settings → Advertisers tab gets demoted here.
- [Embedded-mode rename](./embedded-mode-rename.md) — wire-shape
  compatibility for upstream `prebid/salesagent` adopters.
