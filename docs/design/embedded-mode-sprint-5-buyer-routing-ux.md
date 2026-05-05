# Sprint 5 Spec: Buyer Routing UX overhaul + GAM advertisers cache

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** Sprint 1.8 (routing rules + default advertiser),
Sprint 4 (UI hardening)
**Status:** Captured (full overhaul + deployment plan)
**Last updated:** 2026-05-04

## Vocabulary pin: agent vs operator vs brand

Three distinct axes — this sprint surfaces all three; Sprint 1.8 only
modeled two and that gap motivates the schema extension below.

- **Agent (Principal)** — who's making the API call. Authenticated
  identity. Embedded: always the host (Scope3 / Interchange).
  Standalone: many possible agents (Wonderstruck, future buyers).
  PSA term: ``principal_id``. AdCP-protocol term: agent / buyer.

- **Operator** — the entity operating on behalf of the brand
  (e.g. WPP running media for Coca-Cola). When the brand operates
  directly, operator = brand domain. From AdCP
  ``AccountReference2.operator``.

- **Brand** — the advertiser brand: ``domain`` + optional
  ``brand_id``. From AdCP ``AccountReference2.brand``.

The buyer-protocol call carries operator + brand in the request
body (``AccountReference``); the agent comes from the auth context
(``X-Identity-Buyer-Principal-Id`` header in embedded, bearer token
in standalone). All three together identify "who is buying what for
whom" — and PSA decides the GAM advertiser based on the triple.

### What Sprint 1.8 shipped + the gap

The ``advertiser_routing_rules`` table keys on ``(tenant_id,
operator_domain, brand_house, brand_id)`` — **agent is not in the
natural key**. The chain reads operator + brand from the request
body but ignores who's calling.

In embedded mode this is fine: the agent never varies (the host is
the only buyer), so agent-agnostic routing matches reality.

In standalone or future multi-agent embedded: this is a real gap.
If two agents both buy under WPP/Coke, the publisher gets one
routing rule controlling both — can't bucket them separately.

Sprint 5 closes this gap (see "Schema extension" below) before the
UX overhaul, because the new UX needs to expose the agent column.
Backward-compatible: existing rows get ``principal_id = NULL``
(matches any agent — current behavior preserved).

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
Routing rules                                                  [+ Add rule]
┌──────────────┬─────────────┬──────────────┬───────┬──────────────────────┐
│ Agent        │ Operator    │ Brand house  │ Brand │ GAM advertiser       │
├──────────────┼─────────────┼──────────────┼───────┼──────────────────────┤
│ —            │ wpp.com     │ coca-cola.com│ sprite│ Scope3-WPP-Sprite    │ ← exact
│ —            │ wpp.com     │ coca-cola.com│ —     │ Scope3-WPP-Coke      │ ← brand_id wildcard
│ —            │ wpp.com     │ —            │ —     │ Scope3-WPP           │ ← brand wildcard
│ wstruck-buy  │ publicis.com│ —            │ —     │ Wstruck-Publicis     │ ← agent-specific
└──────────────┴─────────────┴──────────────┴───────┴──────────────────────┘
                                                       em-dash = "any" / NULL
```

In embedded mode the Agent column is hidden in the UI (always renders
as ``—`` since the host is the only agent — no need to clutter).
In standalone the column is visible and editable.

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
Recent activity (last 30 days)                                   [filter: all] ▼
┌───────────────┬──────────┬──────────────┬───────┬─────┬────────────────┬───────┐
│ Agent         │ Operator │ Brand house  │ id    │ Buys│ Advertiser     │ Route │
├───────────────┼──────────┼──────────────┼───────┼─────┼────────────────┼───────┤
│ scope3-emb    │ wpp.com  │ coca-cola.com│ sprite│ 142 │ Scope3-WPP-Spr │ exact │ ← green
│ scope3-emb    │ wpp.com  │ coca-cola.com│ —     │ 38  │ Scope3-WPP-Coke│ house │ ← blue
│ scope3-emb    │ pmi.com  │ —            │ —     │ 12  │ Scope3-Inter   │default│ ← amber
│ wstruck-buy   │ publicis │ random.com   │ —     │ 4   │ Scope3-Inter   │default│ ← amber
└───────────────┴──────────┴──────────────┴───────┴─────┴────────────────┴───────┘
                                                                        [Promote ↑]
```

Agent column visible in standalone (lets publishers spot
"Wonderstruck and Scope3 both hit us under WPP — should they
route differently?"). Hidden in embedded since there's only one.

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

## Schema extension: agent in the routing key

Sprint 1.8 keys ``advertiser_routing_rules`` on
``(tenant_id, operator_domain, brand_house, brand_id)``. Sprint 5
adds ``principal_id`` to that key so standalone publishers can route
different agents to different GAM buckets.

### Migration

```python
# alembic/versions/<rev>_add_principal_id_to_routing_rules.py

def upgrade() -> None:
    op.add_column(
        "advertiser_routing_rules",
        sa.Column("principal_id", sa.String(50), nullable=True),
    )
    # Drop the old COALESCE-unique index, recreate with principal_id
    op.drop_index("uq_routing_rule_natural_key", table_name="advertiser_routing_rules")
    op.create_index(
        "uq_routing_rule_natural_key",
        "advertiser_routing_rules",
        [
            "tenant_id",
            sa.text("COALESCE(principal_id, '')"),
            "operator_domain",
            sa.text("COALESCE(brand_house, '')"),
            sa.text("COALESCE(brand_id, '')"),
        ],
        unique=True,
    )

def downgrade() -> None:
    op.drop_index("uq_routing_rule_natural_key", table_name="advertiser_routing_rules")
    op.create_index(
        "uq_routing_rule_natural_key",
        "advertiser_routing_rules",
        [
            "tenant_id",
            "operator_domain",
            sa.text("COALESCE(brand_house, '')"),
            sa.text("COALESCE(brand_id, '')"),
        ],
        unique=True,
    )
    op.drop_column("advertiser_routing_rules", "principal_id")
```

Backward-compatible: existing rules get ``principal_id = NULL``
(matches any agent — preserves Sprint 1.8 behavior).

### Resolution chain extension

Most-specific-wins. Agent-specific rules beat agent-agnostic rules
at every brand specificity tier:

```
1.  agent + operator + brand_house + brand_id     (most specific)
2.  agent + operator + brand_house + NULL
3.  agent + operator + NULL + NULL
4.  NULL  + operator + brand_house + brand_id     (any agent, exact brand)
5.  NULL  + operator + brand_house + NULL
6.  NULL  + operator + NULL + NULL                (operator wildcard, current Sprint 1.8 behavior)
7.  Tenant.default_gam_advertiser_id
8.  raise TENANT_NOT_ACTIVATED                    (least specific)
```

Sandbox carve-out still short-circuits at the top (unchanged).

Embedded tenants only ever populate levels 4-6 + 7 since their
agent is fixed — same UX as Sprint 1.8.

### API surface change

``BuyerAdvertiserMapping`` Pydantic model gains ``principal_id:
str | None``. ``CreateBuyerAdvertiserMappingRequest`` /
``UpdateBuyerAdvertiserMappingRequest`` accept it. ``GET
/buyer-advertiser-mappings`` returns it.

The 409 ``routing_rule_duplicate`` error message gains
``principal_id`` to the details block — same shape as the existing
3-axis duplicate detail.

### Account.resolved_via enum

Stays as-is (``account | sandbox | exact | house | operator | default``).
The chain resolution semantics carry through; we don't differentiate
"agent-specific exact" from "agent-agnostic exact" in the stamp
because the rule that matched is the source of truth — Storefront
can read the matched rule's ``principal_id`` if it needs to surface
the distinction in the activity view.

## Parallelization

Six workstreams. Dependency graph:

```
A0. principal_id schema extension                    ─┐
A.  gam_advertisers cache (table + sync + endpoint)  ─┤
                                                       ├─► C. Editor (default + rule CRUD)  ─┐
B.  UI scaffolding (new page, nav, read-only)        ─┤                                       ├─► E. Promote-to-rule
                                                       └─► D. Activity view                  ─┘
```

**Workstream A0 — principal_id in routing-rule natural key (~0.5 days)**
Migration + model column + Pydantic schema field + chain resolution
extended from 4 levels to ~6 levels. Backend-only. Backward-compatible
(NULL = any agent — preserves current Sprint 1.8 behavior). Tests
extend the existing routing-chain matrix to include agent-specific +
agent-agnostic rule precedence.

**Workstream A — gam_advertisers cache (~1.5 days)**
Backend-only. Migration, model, sync_advertisers worker function,
`GET /gam/advertisers` endpoint, tests. Depends on nothing else.

**Workstream B — UI scaffolding (~0.5 days)**
New blueprint route `/tenant/<id>/buyer-routing`, new template
`buyer_routing.html` with the three-section skeleton, nav entry,
all read-only initially (just renders existing data via existing
endpoints). Depends on nothing — uses already-shipped APIs.

**Workstream C — Editor: default + rule CRUD (~1 day)**
Wires the picker, modals, save handlers, agent column visibility
gate (hidden in embedded, visible in standalone). Depends on A
(advertiser picker needs the cache), A0 (rules carry principal_id),
and B (skeleton).

**Workstream D — Activity view (~0.5 days)**
Renders `/recent-buyers` as a sortable table with `resolved_via`
color-coding + filter dropdown + agent column. Pagination wired to
`?days` / `?limit`. Depends on B (skeleton); fully parallel with C.

**Workstream E — Single-click promote (~0.5 days)**
Adds the promote action on Activity rows that opens the rule editor
prefilled with the (agent, operator, brand) triple. Depends on C
(editor exists) + D (rows clickable).

**Total scope: ~4.5 days** if parallelized; ~5.5 days serial.

A0, A, and B can start in parallel today. C waits for A + A0; D can
start with B and run in parallel with C. E is the final convergence.

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

### Constraint check (corrected from earlier draft)

Original draft of this section claimed the
``managed_externally → is_embedded`` rename ruled out same-DB
deployments. **That was wrong.** Verification:

- ``managed_externally`` was introduced in commit ``12515ed7``
  ("Sprint 1 of managed tenant mode") — on THIS branch, never on
  ``prebid/salesagent:main``
- ``is_embedded`` is the renamed form, also only on this branch
- The OLD Fly deployment runs upstream-ish code from BEFORE Sprint 1.
  It doesn't reference either column name — it doesn't know
  embedded mode exists.

So the deployment doesn't have a column-incompatibility problem.
The new code (under either name) can share a database with the old
Fly code, because the old code never reads/writes the embedded-mode
columns regardless of what they're called.

This unblocks Option 4 (cross-cloud DB) as a viable path.

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

**Option 4: Fresh GCP deployment + same Fly database via cross-cloud DB connection** ✅ **recommended for validation phase**
- New PSA on GCP Cloud Run (or GKE), pointing at the existing Fly
  Postgres via cross-cloud connection
- Old Fly app keeps running against the same DB — both apps can read/
  write because the schema column-rename incompatibility doesn't
  exist (see "Constraint check" above)
- DNS-weighted cutover: 1% → 10% → 50% → 100% to GCP
- **Rollback = repoint DNS to Fly app. Seconds, no data loss.**
- Latency cost: ~50-100ms per query (Fly DB → GCP app). Fine during
  validation; matters more under scale
- Doesn't solve eventual migration off Fly — but defers it until
  the GCP app is proven, at which point the cutover (Option 5) is
  a planned activity rather than a leap of faith

**Option 5: Fresh GCP deployment + GCP Cloud SQL, migrate Wonderstruck** ✅ recommended *after* Option 4 validation
- After GCP app is proven via Option 4: `pg_dump` Fly → `pg_restore`
  Cloud SQL → flip app config to point at Cloud SQL → minor downtime
  for the cutover (~minutes given data volume)
- Now everything is co-located on GCP; cross-cloud latency cost
  goes away
- Decommission Fly deploy
- Sequenced after Option 4 = much less risky than Option 5
  unilaterally — we already know the GCP app works against real
  data when we make the DB cutover decision

### Fork question

Two pressures pull in different directions:
- **Stay on upstream `prebid/salesagent`**: free upstream contributions
  flowing in; everyone benefits from upstream fixes; embed-mode work
  becomes the canonical multi-tenant story for the project
- **Fork to `agenticadvertising/salesagent` (or similar)**: control
  release timing; can ship ahead of upstream review; long-running PRs
  don't block production

**Recommended hybrid:**

1. **Fork now** to ``agenticadvertising/salesagent`` (or wherever
   we want the canonical embedded-mode line to live). Branch
   ``embedded-mode-v1`` on the fork; deploy from there.

2. **Open one large PR** ``agenticadvertising/salesagent:embedded-mode-v1``
   → ``prebid/salesagent:main`` for visibility. The PR is a
   communication artifact: "here's the embedded-mode work, here's
   the design rationale, you can review when ready." It's not a
   gate on our deployments.

3. **Deploy directly from the fork branch.** The PR existing or not
   has zero impact on what runs in production. Long-running PR /
   long-running fork branch is the same operational picture either
   way: a feature branch we control, deployed via our own CD.

4. **Periodic upstream sync.** Weekly cron: rebase / merge upstream
   ``main`` into our ``embedded-mode-v1``, run the test suite, fix
   conflicts. If upstream is quiet, this is a no-op; if upstream
   ships big work, we keep up incrementally rather than facing a
   six-month diff at the end.

5. **Three end-states, all handled cleanly:**
   - Upstream merges → our fork branch matches upstream main →
     keep deploying from upstream main, retire the fork branch
   - Upstream rejects/never reviews → we own the canonical
     embedded-mode line on the fork; eventually propose as PSA 2.0
     separately, by which time the work is battle-tested in prod
   - Upstream forks the project itself (rare) → we're already on a
     fork, just pick which upstream to track

Cost of the fork: a periodic upstream-sync cron + the discipline to
actually run it. Cheap relative to the alternative (blocking on
upstream review for production rollouts).

**On reverting the rename:** ``managed_externally`` was net-new on
this branch (never shipped upstream); ``is_embedded`` is the
renamed form, also only here. Either name is purely internal
choice. Recommend keeping ``is_embedded`` (clearer name, work is
done, the alias fields in the Pydantic schemas cost nothing). If
the upstream PR review pushes back on the rename, we can revert it
in a single commit later — it doesn't change deployment posture.

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
