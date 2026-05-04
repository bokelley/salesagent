# Proposal: Managed Tenant Mode

**Status:** Draft v2
**Author:** Brian O'Kelley
**Last updated:** 2026-05-04

## Summary

The salesagent runs in two operational modes:

- **Open instance** (today's behavior): public buyer protocols, salesagent admin UI for direct-customer publishers, mix of self-managed tenants.
- **Managed instance** (new): no public surfaces. The salesagent is a Scope3-internal service. Publishers reach it only through Scope3 Storefront via reverse proxy. Buyer-side traffic comes from the Scope3 buyer agent on the private network.

In a managed instance, configuration responsibility splits along **infrastructure vs. business** lines:

- **Platform-managed** (Scope3 owns, locked to the Tenant Management API): tenant lifecycle, billing, adapter credentials, external identity, network/domain config, OIDC. The publisher never touches these.
- **Publisher-managed** (publisher owns, writable through the proxied UI): products, principals/advertisers, creatives, workflow approvals, authorized properties, business rules, slack config, agents, policy.

The salesagent is a runtime *for the buyer protocol* and a *bounded operations UI* for the publisher. Scope3's control plane owns the platform plumbing. The publisher operates day-to-day via Scope3 Storefront, which proxies the salesagent UI for everything in their lane.

## Motivation

Today the salesagent is shaped as a self-service product: publishers log into the admin UI, configure their own tenant (subdomains, Slack, OIDC, business rules, products, principals, GAM credentials), and operate it directly. This works for direct customers but is wrong for the Scope3 partnership model.

In the Scope3 partnership, publishers should never see a separate salesagent surface — not the URL, not the UI, not the auth flow. They configure their sales agent through Scope3 Storefront. Scope3 sends configuration over an internal API, proxies the operations console, and routes buyer traffic from its buyer agent. The salesagent has no externally visible footprint.

Today's `src/admin/tenant_management_api.py` is a partial step: 6 endpoints under `/api/v1/tenant-management`, API-key authenticated, hand-rolled Flask. It covers ~10% of what a control plane needs. The other 90% lives in 23 Flask UI blueprints (~210 routes, ~693 inline DB access calls), which are inaccessible programmatically.

## Goals

1. Scope3's control plane can provision a salesagent tenant and own all platform-level configuration via API only — no human ever needs to log into the salesagent's own URL for platform setup.
2. Publishers reach a *bounded* operations UI via `scope3.com/storefront/salesagent/...`, authenticated by Scope3's SSO. They never see a salesagent URL. They can self-serve everything in the publisher-managed scope (products, principals, creatives, workflows, etc.).
3. Buyer-side MCP/A2A endpoints have no public exposure on a managed instance — only Scope3's buyer agent on the private network reaches them.
4. The Tenant Management API has a published OpenAPI spec so Scope3 can generate a typed client. The API exposes both platform-managed *and* publisher-managed surfaces, so Scope3 can automate or bulk-edit either if it wants — but the publisher-managed surfaces aren't blocking sprint deliveries because the UI handles them.
5. Salesagent staff retain a private super-admin backdoor for ops/debugging.
6. Existing direct-customer tenants on open instances continue working unchanged.

## Non-goals

- A full REST refactor of the admin UI. Open instances keep their Flask UI handlers untouched.
- Replacing buyer-facing MCP/A2A. The protocol stays as-is — managed mode just controls *who can reach it*.
- Multi-tenant control plane in v1. A managed instance has one control plane (Scope3) authenticated with one global API key.
- A Scope3-native UI. v1 reverse-proxies the existing salesagent admin UI. Scope3 may build a native UI later on the Tenant Management API; that's a separate project.

## Architecture overview

```
                   ┌─────────────────────────────────────┐
                   │           Scope3 Storefront         │
                   │  ┌───────────────────────────────┐  │
                   │  │  Publisher UI (operations)    │  │
                   │  │  /storefront/salesagent/...   │──┼──reverse proxy──▶ salesagent admin UI
                   │  └───────────────────────────────┘  │     (private)
                   │  ┌───────────────────────────────┐  │
                   │  │  Scope3 Control Plane         │──┼──API key───────▶ Tenant Management API
                   │  │  (provisioning, config)       │  │     (private)
                   │  └───────────────────────────────┘  │
                   │  ┌───────────────────────────────┐  │
                   │  │  Scope3 Buyer Agent           │──┼──bearer token──▶ MCP / A2A
                   │  │  (calls salesagent per buy)   │  │     (private)
                   │  └───────────────────────────────┘  │
                   └─────────────────────────────────────┘

Salesagent staff ──Google OAuth──▶ Salesagent admin URL  (private network only)
                                          (super-admin backdoor for ops)
```

All three communication paths between Scope3 and the salesagent are internal-only on a managed instance. The salesagent has no public hostname.

## Proposal

### 1. Instance mode + tenant flag

Two layered controls:

- **Instance mode** (`MANAGED_INSTANCE=true|false`, env var, default `false`): drives network policy. When `true`, public buyer-protocol endpoints reject external traffic; the salesagent expects to live behind a private network with Scope3 as the only client.
- **Tenant flag** (`Tenant.managed_externally: bool`): drives platform-config locking and UI nav scoping on a per-tenant basis. In a managed instance, every tenant gets `managed_externally=true` automatically. The flag also exists on open instances for any tenant the operator wants to put under external control.

The two-layer design lets future deployments use managed mode without locking down their entire instance. For the Scope3 deployment, both are on, all the time.

### 1a. Platform-managed vs. publisher-managed surfaces

The defining design choice of managed mode: only *platform-managed* surfaces are locked to the Tenant Management API. *Publisher-managed* surfaces remain writable via the proxied UI so publishers can do their job without round-tripping through Scope3.

| Platform-managed (Tenant Management API only — UI is read-only with a banner) | Publisher-managed (UI writable, also exposed via API for automation) |
|---|---|
| `Tenant` core: name, billing_plan, is_active, managed_externally, external_org_id, external_source | Products (CRUD, autogenerate-from-GAM) |
| `AdapterConfig` (GAM creds, network code, etc.) | Principals / advertisers (CRUD, token rotation) |
| Subdomain / domain config (largely unused in managed mode anyway) | Creatives (review, approve, reject) |
| OIDC config (unused — identity comes from upstream platform) | Workflow approvals |
| Initial provisioning defaults (currency, default property tag) | Authorized properties (post-initial-setup) |
| Tenant lifecycle (provision / deactivate / delete) | Inventory profiles |
| | Creative agents, signals agents |
| | Slack config (publisher's own webhook) |
| | Business rules, policy |

The boundary is *infrastructure vs. business*. Infrastructure (how the tenant connects, who it is externally, what plan it's on) — the platform cares; lock it. Business (what products the publisher offers, who their advertisers are, what they accept) — the publisher cares; let them do their job.

Enforcement is a **scoped write guard** at the model layer (see [sprint 1 spec](./managed-tenant-mode-sprint-1.md)): only the platform-managed columns/tables raise `ManagedTenantWriteError` when written from anywhere except the Tenant Management API. Publisher-managed tables are unaffected.

### 2. Authentication: identity propagation from the platform edge

The salesagent does not authenticate publisher users itself in managed mode. Authentication happens at the upstream platform's edge (Scope3 Storefront uses Google OIDC; another platform might use something else). The platform forwards the authenticated identity to the salesagent as trusted headers on the proxied request.

The salesagent defines this identity-propagation contract as a deployment requirement of managed mode. Any platform running the salesagent in managed mode — Scope3 or otherwise — implements it. The contract:

```
X-Identity-Email      string, required
X-Identity-Org-Id     string, required
X-Identity-Role       enum: admin | member | viewer, required
X-Identity-Source     string, required (identifies the upstream platform)
X-Identity-User-Id    string, optional (stable upstream user ID for audit)
X-Identity-Signature  optional, present if the platform signs identity (HMAC/JWT)
```

The salesagent's job is small: a middleware that reads the headers, maps `X-Identity-Org-Id` to a tenant via `Tenant.external_org_id`, derives the salesagent role from `X-Identity-Role`, and scopes the request. No signature verification in v1 — trust is established by the network (the salesagent is reachable only through the upstream platform's authenticated proxy). If a deployment later requires signed identity, the salesagent flips a config knob (`IDENTITY_TRUST_MODE = network | signed`) and verifies `X-Identity-Signature`. Same middleware, same header schema, no protocol change.

`X-Identity-Source` lets the salesagent know which platform forwarded the request (e.g., `scope3`, `acme-storefront`). Useful for audit logs and for cases where one salesagent instance might serve more than one upstream platform.

No salesagent-side `User` records for managed tenants. Identity is ephemeral — every request re-reads the headers. The audit log captures email/org/user-id from the headers for traceability.

The salesagent's existing OIDC blueprint (`src/admin/blueprints/oidc.py`) is unused on managed instances; per-tenant OIDC config is for open instances.

**Org-picker, multi-org users**: handled entirely by the platform before the request reaches the salesagent. By the time the proxy forwards, one org is selected. Salesagent never sees ambiguity.

**Listener hardening on managed instances** (required, not optional, since trust is network-based):
- Bind to a private interface only — never `0.0.0.0`.
- Allow-list the upstream proxy's source IP/range at the salesagent's listener.
- Reject any request missing the required `X-Identity-*` headers — fail closed.
- Audit-log the headers on every request for post-hoc detection.

**Super-admin backdoor**: salesagent staff log into the salesagent's direct URL (private network only — VPN or office IP) using Google OAuth restricted to `SUPER_ADMIN_EMAILS`. This bypass exists for ops, debugging, incident response. It does not depend on the platform's identity contract and works regardless of Scope3 availability.

### 3. Reverse proxy

`scope3.com/storefront/salesagent/{tenant_id}/...` → `salesagent.internal/tenant/{tenant_id}/...`. Scope3's nginx forwards; salesagent serves HTML.

The salesagent already supports path-prefix mounts (CLAUDE.md pattern #6: `request.script_root` in Python, `scriptRoot` in JS). Running behind Scope3's proxy is the same shape with a different prefix. Required:

- `ProxyFix` middleware for `X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Forwarded-Prefix`.
- All template URLs and JS fetch calls already use `script_root` — verify no hardcoded URLs slipped in.
- Cookies are not used for managed-tenant auth (the JWT is the credential). No cross-domain cookie problems.

iframe was an alternative — rejected. CSP, X-Frame-Options, history/deep-linking pain. Reverse proxy is materially better and the codebase is already shaped for it.

### 4. Subdomain routing

Closed for managed instances. Tenant identity comes from the URL path (`/tenant/{tenant_id}/...`) and the JWT claim, validated to match. `src/core/domain_config.py` and the approximated.app integration are unused on managed instances. Open instances keep them. This is a major simplification of the routing layer for the Scope3 deployment.

### 5. Comprehensive Tenant Management API

Extend the existing `/api/v1/tenant-management` blueprint into a complete configuration surface. Every knob a publisher can set in today's UI must be settable via the API. The salesagent does not pre-decide what's "advanced" or "exposable" — Scope3 chooses what to expose to its users.

```
# Tenant lifecycle
POST    /tenants/provision               # one-shot: create + configure + tokens + external_org_id
GET     /tenants
GET     /tenants/{id}
PATCH   /tenants/{id}
POST    /tenants/{id}/deactivate
POST    /tenants/{id}/reactivate
DELETE  /tenants/{id}

# Adapter
GET/PUT /tenants/{id}/adapter-config
POST    /tenants/{id}/adapter-config/test-connection

# Principals (advertisers)
GET/POST          /tenants/{id}/principals
GET/PATCH/DELETE  /tenants/{id}/principals/{pid}
POST              /tenants/{id}/principals/{pid}/rotate-token

# Products
GET/POST          /tenants/{id}/products
PATCH/DELETE      /tenants/{id}/products/{pid}
POST              /tenants/{id}/products/autogenerate-from-gam

# Inventory & properties
GET/POST/DELETE  /tenants/{id}/property-tags[/{tag_id}]
GET/POST/DELETE  /tenants/{id}/authorized-properties[/{prop_id}]
GET/POST/DELETE  /tenants/{id}/inventory-profiles[/{prof_id}]

# Other config sub-resources
GET/PUT  /tenants/{id}/currency-limits
GET/PUT  /tenants/{id}/slack-config
GET/PUT  /tenants/{id}/business-rules
GET/PUT  /tenants/{id}/policy
GET/POST/DELETE  /tenants/{id}/creative-agents[/{agent_id}]
GET/POST/DELETE  /tenants/{id}/signals-agents[/{agent_id}]

# Read-only operational state
GET     /tenants/{id}/status
GET     /tenants/{id}/workflows
POST    /tenants/{id}/workflows/{wid}/approve
POST    /tenants/{id}/workflows/{wid}/reject
GET     /tenants/{id}/media-buys
GET     /tenants/{id}/audit-log
GET     /tenants/{id}/sync-status

# Spec
GET     /openapi.json
GET     /docs                            # Swagger UI
```

**~50 endpoints**, all thin CRUD over Pydantic schemas.

The marquee endpoint is `POST /tenants/provision`. It bundles tenant creation + adapter configuration + initial principal + initial products + token issuance + `external_org_id` stamping into one call. Returns tenant ID, MCP URL, A2A URL, principal API tokens, and the storefront URL Scope3 should link to.

Endpoints removed from this list compared to v1 of the proposal:
- `/oidc-config` — managed instances use the global Scope3 SSO config, not per-tenant OIDC.
- `/users` — Scope3 owns publisher-side users; no salesagent User records for managed tenants.
- `/domains` — no per-tenant subdomains in managed mode.
- `/favicon`, `/slack/test` and similar UI-utility endpoints — operations console reads them via the read endpoints; mutations not needed in managed mode.

### 6. OpenAPI from day one

Adopt **`spectree`**: Pydantic request/response models per endpoint, served at `/openapi.json` with Swagger UI at `/docs`.

- Repo already lives on Pydantic; spectree fits.
- Stays in Flask — no FastAPI migration for one slice.
- Forces request/response schema discipline.
- Scope3 generates a typed client from the spec.

Alternatives rejected: hand-written `openapi.yaml` (drifts from code); FastAPI for this slice (overkill, two web frameworks).

### 7. UI behavior on managed instances

Middleware checks `managed_externally` on every tenant-scoped request. The UI is *not* fully read-only — it's *bounded* to the publisher-managed scope.

**Platform-managed pages** (settings → general, settings → adapter, settings → domains, settings → OIDC, account/billing, tenant lifecycle):
- Render read-only with the banner: *"Platform settings managed by Scope3 Storefront."*
- Nav entries hidden by default; visible only to super-admin backdoor users for debugging.
- Mutation routes return 403 even if reached directly via URL (the model-layer guard catches anything that slips through middleware).

**Publisher-managed pages** (products, principals, creatives, workflows, properties, inventory profiles, agents, slack, business rules, policy):
- Fully writable through the UI. No banner, no friction.
- Same routes as today's open-instance UI. No code path divergence beyond the platform-page hiding.

**Operational pages** (dashboard, media-buy viewer, audit log, sync status):
- Always read-only by nature. Identical for managed and unmanaged tenants.

The super-admin backdoor sees all tenants without restriction — managed mode does not block salesagent staff. They can still write to platform-managed surfaces directly via super-admin tools (the model guard checks for the Tenant Management API session flag *or* a super-admin escape flag).

### 8. Network surface (managed instance)

When `MANAGED_INSTANCE=true`:

- Buyer protocol endpoints (`/mcp/`, `/a2a`) accept traffic only from the configured private network range (configurable via `BUYER_PROTOCOL_ALLOWED_CIDR` or similar). Public traffic gets `403`. **No protocol-level auth** — callers identify the principal/tenant via the same `X-Identity-*`/`X-Principal-Id` header contract used for the UI proxy. Network is the trust boundary.
- Tenant Management API (`/api/v1/tenant-management`) same network restriction. API key required (the one credential that crosses the boundary on purpose, identifying the control plane).
- Salesagent admin URL same network restriction (super-admin backdoor reachable via VPN or internal hostname only).
- Reverse-proxied UI traffic from Scope3 Storefront comes through Scope3's network — also private.

Net result: zero public exposure for the entire salesagent on a managed instance, and zero per-principal credentials being passed around. Identity flows as headers; trust is established by the network.

**Open-instance behavior is unchanged.** Public MCP/A2A keeps `x-adcp-auth` bearer tokens per principal. The mode is selected by `MANAGED_INSTANCE`; the salesagent's `resolve_identity()` branches on it.

### 9. Webhooks (optional, post-v1)

For Scope3 to surface live state without polling, add outbound webhooks: workflow created/approved/rejected, sync failed, media buy delivered, adapter connection lost. Signed payloads, at-least-once with retry. Not required for v1 — polling `GET /status` and `GET /workflows` is sufficient.

## Phasing

| Sprint | Deliverable |
|---|---|
| Sprint | Deliverable | Required for launch? |
|---|---|---|
| **1** | **Full platform-managed surface via API.** Migrations (`managed_externally`, `external_org_id`, `external_source` on Tenant; external identity fields on AuditLog). `MANAGED_INSTANCE` env. Scoped write guard at the model layer. spectree wired up. Tenant lifecycle endpoints (provision, list, get, patch, deactivate, reactivate, delete). Adapter management endpoints (get, put, test-connection). Identity-header reader middleware. Reverse-proxy compatibility verified. Swagger UI live. *After this sprint, Scope3 can fully manage tenants via API.* | yes |
| **1.5** | **Storefront integration essentials.** `POST /tenants/preview-adapter` (test creds + return network metadata before provisioning). `GET /tenants/{tid}/status` (consolidated operational status — adapter, syncs, workflows, media-buys, packages, creatives, webhooks). Identity-propagation contract sign-off as a stable integration spec. *Unblocks Scope3 Storefront UX.* | yes |
| **2** | **Runtime hardening.** UI middleware that scopes nav by `managed_externally`, hides platform-config pages, renders banners. Network policy for `MANAGED_INSTANCE` (CIDR allow-lists; fail-closed on missing config). `resolve_identity()` change for MCP/A2A in managed mode (header-scoped, no per-principal tokens). Super-admin override path. *After this sprint, the system is safely deployable in managed mode.* | yes |
| **3** | **Workflow mutations + drill-down reads.** Workflow approve/reject. List + detail endpoints for workflows, media-buys, audit-log. Sync history. Backs the `GET /status` summary with detail views Scope3 can drill into. | yes |
| **4 (optional)** | **Publisher-managed CRUD via API.** Principals + Products + autogenerate-from-GAM. Automation conveniences — publishers also do these via the proxied UI. | only if needed |
| **5 (optional)** | **Remaining publisher-managed sub-resources via API.** Tags, authorized properties (incl. bulk import), inventory profiles, currency limits, slack, business rules, policy, creative agents, signals agents. | only if needed |
| **6 (optional)** | **Outbound webhooks.** Scope3 receives signed payloads on state changes; replaces polling load. | only if needed |

**Sprints 1, 1.5, 2, 3 are the required path** — they deliver everything Scope3 needs for a managed-mode launch. Each is independently shippable.

Sprints 4–6 are optional automation conveniences. They become relevant if Scope3 wants programmatic publisher-side management (sprints 4–5) or near-real-time push notifications (sprint 6). Defer until there's a concrete need.

## Implementation notes

### UI ↔ API mapping: shared business logic, not HTTP self-calls

Every endpoint in the Tenant Management API has a corresponding UI handler today (the publisher-managed surfaces) or could have one. The two transports must not contain duplicated business logic — that's how bugs diverge.

**The pattern**: both UI handler and API endpoint delegate to the same repository / `_impl()` function. Transport adapters around shared logic.

```
    UI route handler  ───┐                             ┌───  API endpoint
    Flask blueprint      │                             │     spectree+Pydantic
    template render      ▼                             ▼     JSON in/out
                  ┌──────────────────────────────────────────────┐
                  │   Repository / _impl()                       │
                  │   src/core/repositories/...                  │
                  │   src/core/services/_*_impl()                │
                  │   ── single source of truth ──               │
                  └──────────────────────────────────────────────┘
                                       │
                                       ▼
                                    ┌─────┐
                                    │ DB  │
                                    └─────┘
```

This mirrors the buyer-side MCP/A2A pattern (CLAUDE.md transport-boundary rule) and satisfies the existing structural guards (boundary completeness, no transport imports in `_impl`, etc.).

**Why not "UI is a SPA over the API"** (option C in the trade tree): loses server-side template rendering, adds HTTP latency for self-calls, requires building real SPA infrastructure for what is effectively an internal admin tool. Wrong choice for this codebase.

### Greenfield extraction, sprint by sprint

The 23 admin UI blueprints contain ~693 inline DB access patterns and only 4 references to `_impl()`-style transport-agnostic functions. We do **not** refactor all of them up front.

**Recommended path:** for every endpoint a sprint adds:
1. Extract `_impl()` / repository function (most don't exist yet).
2. Build the API endpoint as a thin wrapper.
3. **Opportunistically** refactor the corresponding UI handler(s) to call the same function.

If the optional publisher-managed sprints (4–5) ship, the publisher-managed UI handlers naturally converge to shared business logic alongside their API endpoints. Until then, only platform-managed surfaces (sprints 1, 1.5) and operational surfaces (sprint 3) get the extraction. The unmanaged-only surfaces (open-instance customer config, etc.) can stay inline indefinitely — allowlisted in the structural-guard FIXME registry and shrink over time.

**Why not refactor everything first:** would block API delivery on a multi-sprint refactor that ships zero value to Scope3 until the end.

## Open questions

1. **Identity contract canonical doc location.** The `X-Identity-*` header schema is owned by the salesagent and consumed by upstream platforms. Should it live as a top-level integration spec (e.g., `docs/integration/managed-mode-identity-contract.md`) referenced from this design, so platforms can read it without wading through the design rationale?
2. **One external org → one salesagent tenant.** True for now, but design should leave room — if a publisher has multiple GAM networks, they may want multiple tenants under one org. Probably modeled as `Tenant.external_org_id` (non-unique, with a "primary" tenant for ambiguous routes) but defer until needed.
3. **Conflict resolution.** What happens if a super-admin manually edits a managed tenant's DB row directly? Hard-block at the model layer (`Tenant.save()` rejects writes when `managed_externally=true` and caller isn't the Tenant Management API), or trust the middleware? Recommend hard-block — middleware drift is a class of bug not worth tolerating.
4. **Audit trail for managed tenants.** With no User table, every mutation is "by control plane" or "by upstream user X (email Y, org Z)". Schema change to `AuditLog`: optional `external_user_email`, `external_user_id`, `external_org_id`, `external_source`.
5. **Migration strategy for existing tenants.** If you decide later to migrate a direct-customer tenant to managed mode, what's the cutover? Probably: `PATCH /tenants/{id}` with `managed_externally=true` + `external_org_id`, then forward future writes through Scope3. Document but don't build tooling in v1.

## Risks

- **Two control planes during transition.** Until middleware ships in sprint 2, both Scope3 and any direct admin-UI access can write to managed tenants on platform-managed surfaces. Mitigation: the model-layer write guard ships in sprint 1, providing a hard backstop even before middleware lands.
- **API surface drift.** As the UI evolves for unmanaged tenants on open instances, new config knobs may not get API endpoints. Mitigation: structural guard requiring every new field on a config model to have a corresponding API endpoint or an explicit `managed_unsupported=true` annotation.
- **Reverse-proxy URL leakage.** If any salesagent template hardcodes URLs, they'll point at the wrong host when proxied. Mitigation: audit templates and JS for hardcoded paths in sprint 1; the `script_root` pattern is already enforced but not in tests.
- **Super-admin lockout.** If the salesagent's private network connectivity breaks, ops can't reach the backdoor either. Mitigation: ensure the backdoor URL is reachable via at least two paths (VPN + bastion).

## Decision

Pending. Next step if accepted: detailed Pydantic schema spec for `POST /tenants/provision` and the JWT verification design.
