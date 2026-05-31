# Proposal: Embedded Mode

**Status:** Draft v3
**Author:** Brian O'Kelley
**Last updated:** 2026-05-17

> **Reference deployment.** Embedded mode is the deployment shape that lets PSA run *inside* a host product — an SSP console, a publisher tools SaaS, a wrapper-management service, anything that already authenticates publishers and wants to add a sales agent without sending users to a second URL. Scope3 Storefront is the first reference deployment and is named in concrete examples throughout. Nothing in the design is Scope3-specific.

## Summary

The salesagent runs in two operational modes:

- **Open instance** (today's behavior): public buyer protocols, salesagent admin UI for direct-customer publishers, mix of self-managed tenants.
- **Embedded instance** (new): no public surfaces. The salesagent runs as a private service behind a host product. Publishers reach it only through the host's UI via reverse proxy. Buyer-side traffic comes from the host's buyer agent on the private network.

In an embedded instance, configuration responsibility splits along **infrastructure vs. business** lines:

- **Platform-managed** (host product owns, locked to the Tenant Management API): tenant lifecycle, billing, adapter credentials, external identity, network/domain config, OIDC. The publisher never touches these.
- **Publisher-managed** (publisher owns, writable through the proxied UI): products, principals/advertisers, creatives, workflow approvals, authorized properties, business rules, slack config, agents, policy.

The salesagent is a runtime *for the buyer protocol* and a *bounded operations UI* for the publisher. The host product's control plane owns the platform plumbing. The publisher operates day-to-day via the host product's UI, which proxies the salesagent UI for everything in their lane.

## Motivation

Today the salesagent is shaped as a self-service product: publishers log into the admin UI, configure their own tenant (subdomains, Slack, OIDC, business rules, products, principals, GAM credentials), and operate it directly. This works for direct customers but is wrong for any host product that wants to embed PSA inside its own surface.

A host product wants its publishers to never see a separate salesagent surface — not the URL, not the UI, not the auth flow. They configure their sales agent through the host's UI. The host sends configuration over an internal API, proxies the operations console, and routes buyer traffic from its buyer agent. The salesagent has no externally visible footprint.

Every host product that has tried to embed PSA so far has rebuilt the same shims — reverse-proxy the Flask UI, hack auth around their own SSO, intercept the tenant API. Embedded mode is what falls out of doing that work *once*, in PSA core, with the host-product needs as a first-class deployment shape.

Today's `src/admin/tenant_management_api.py` is a partial step: 6 endpoints under `/api/v1/tenant-management`, API-key authenticated, hand-rolled Flask. It covers ~10% of what a control plane needs. The other 90% lives in 23 Flask UI blueprints (~210 routes, ~693 inline DB access calls), which are inaccessible programmatically.

## Goals

1. A host product's control plane can provision a salesagent tenant and own all platform-level configuration via API only — no human ever needs to log into the salesagent's own URL for platform setup.
2. Publishers reach a *bounded* operations UI under the host product's domain (e.g., `host.example.com/path/salesagent/...`), authenticated by the host's SSO. They never see a salesagent URL. They can self-serve everything in the publisher-managed scope (products, principals, creatives, workflows, etc.).
3. Buyer-side MCP/A2A endpoints have no public exposure on an embedded instance — only the host's buyer agent on the private network reaches them.
4. The Tenant Management API has a published OpenAPI spec so any host can generate a typed client. The API exposes both platform-managed *and* publisher-managed surfaces, so the host can automate or bulk-edit either if it wants — but the publisher-managed surfaces aren't blocking sprint deliveries because the UI handles them.
5. Salesagent staff retain a private super-admin backdoor for ops/debugging.
6. Existing direct-customer tenants on open instances continue working unchanged.

## Non-goals

- A full REST refactor of the admin UI. Open instances keep their Flask UI handlers untouched.
- Replacing buyer-facing MCP/A2A. The protocol stays as-is — embedded mode just controls *who can reach it*.
- Multiple host products on the same embedded instance in v1. An embedded instance has one control plane authenticated with one global API key. Multi-host on one instance is a future extension; the `X-Identity-Source` header is the seam for it.
- A host-native UI. v1 reverse-proxies the existing salesagent admin UI. A host may build a native UI later on the Tenant Management API; that's a separate project per host.

## Product policy: explicit opt-in for ad-server writes

Embedded mode is permissive about *reads* (the salesagent fetches GAM inventory, advertiser lists, network metadata as part of normal operation) and conservative about *writes that create new state in the publisher's ad server*. Specifically: the salesagent does **not** auto-create GAM advertisers on first buy unless the publisher (or the host product on the publisher's behalf) has explicitly opted in per tenant. This is the default for every tenant — embedded or open.

The mechanism is `Tenant.auto_provision_advertisers` (default `false`). When `false`, an unmapped buy returns `ACCOUNT_NOT_PROVISIONED` and the publisher maps the advertiser explicitly via the Admin UI or API. When `true`, the salesagent calls `CompanyService.createCompanies` on the publisher's GAM network on first buy.

We don't presume a default here because we don't yet know what host products / publishers will want, and the cost of guessing wrong (creating unwanted entities in someone else's ad server) is higher than the cost of a one-time per-tenant configuration step.

## Architecture overview

```
                   ┌─────────────────────────────────────┐
                   │             Host Product            │
                   │           (e.g., Scope3 Storefront) │
                   │  ┌───────────────────────────────┐  │
                   │  │  Publisher UI (operations)    │  │
                   │  │  /<host-prefix>/salesagent/…  │──┼──reverse proxy──▶ salesagent admin UI
                   │  └───────────────────────────────┘  │     (private)
                   │  ┌───────────────────────────────┐  │
                   │  │  Host Control Plane           │──┼──API key───────▶ Tenant Management API
                   │  │  (provisioning, config)       │  │     (private)
                   │  └───────────────────────────────┘  │
                   │  ┌───────────────────────────────┐  │
                   │  │  Host Buyer Agent             │──┼──header-scoped▶ MCP / A2A
                   │  │  (calls salesagent per buy)   │  │     (private)
                   │  └───────────────────────────────┘  │
                   └─────────────────────────────────────┘

Salesagent staff ──Google OAuth──▶ Salesagent admin URL  (private network only)
                                          (super-admin backdoor for ops)
```

All three communication paths between the host product and the salesagent are internal-only on an embedded instance. The salesagent has no public hostname.

## Proposal

### 1. Instance mode + tenant flag

Two layered controls:

- **Instance mode** (`MANAGED_INSTANCE=true|false`, env var, default `false`): drives network policy. When `true`, public buyer-protocol endpoints reject external traffic; the salesagent expects to live behind a private network with the host product as the only client.
- **Tenant flag** (`Tenant.is_embedded: bool`): drives platform-config locking and UI nav scoping on a per-tenant basis. In an embedded instance, every tenant gets `is_embedded=true` automatically. The flag also exists on open instances for any tenant the operator wants to put under external control.

The two-layer design lets a deployment use embedded mode for some tenants without locking down its entire instance. A pure embedded deployment (the Scope3 reference) sets both on; a hybrid deployment (some embedded tenants alongside direct customers) sets only the per-tenant flag.

### 1a. Platform-managed vs. publisher-managed surfaces

The defining design choice of embedded mode: only *platform-managed* surfaces are locked to the Tenant Management API. *Publisher-managed* surfaces remain writable via the proxied UI so publishers can do their job without round-tripping through the host product.

| Platform-managed (Tenant Management API only — UI is read-only with a banner) | Publisher-managed (UI writable, also exposed via API for automation) |
|---|---|
| `Tenant` core: name, billing_plan, is_active, is_embedded, external_org_id, external_source | Products (CRUD, autogenerate-from-GAM) |
| `AdapterConfig` (GAM creds, network code, etc.) | Principals / advertisers (CRUD, token rotation) |
| Subdomain / domain config (largely unused in embedded mode anyway) | Creatives (review, approve, reject) |
| OIDC config (unused — identity comes from upstream platform) | Workflow approvals |
| Initial provisioning defaults (currency, default property tag) | Authorized properties (post-initial-setup) |
| Tenant lifecycle (provision / deactivate / delete) | Inventory profiles |
| | Creative agents, signals agents |
| | Slack config (publisher's own webhook) |
| | Business rules, policy |

The boundary is *infrastructure vs. business*. Infrastructure (how the tenant connects, who it is externally, what plan it's on) — the platform cares; lock it. Business (what products the publisher offers, who their advertisers are, what they accept) — the publisher cares; let them do their job.

Enforcement is a **scoped write guard** at the model layer (`src/core/database/embedded_tenant_guard.py`): only the platform-managed columns/tables raise `EmbeddedTenantWriteError` when written from anywhere except the Tenant Management API. Publisher-managed tables are unaffected.

### 2. Authentication: identity propagation from the host product edge

The salesagent does not authenticate publisher users itself in embedded mode. Authentication happens at the host product's edge (Scope3 Storefront uses Google OIDC; another host might use SAML, an internal IDP, etc.). The host forwards the authenticated identity to the salesagent as trusted headers on the proxied request.

The salesagent defines this identity-propagation contract as a deployment requirement of embedded mode. Any host running the salesagent in embedded mode implements it. The contract:

```
X-Identity-Email      string, required
X-Identity-Org-Id     string, required
X-Identity-Role       enum: admin | member | viewer, required
X-Identity-Source     string, required (identifies the host product)
X-Identity-User-Id    string, optional (stable upstream user ID for audit)
X-Identity-Signature  optional, present if the host signs identity (HMAC/JWT)
```

The salesagent's job is small: a middleware that reads the headers, maps `X-Identity-Org-Id` to a tenant via `Tenant.external_org_id`, derives the salesagent role from `X-Identity-Role`, and scopes the request. No signature verification in v1 — trust is established by the network (the salesagent is reachable only through the host's authenticated proxy). If a deployment later requires signed identity, the salesagent flips a config knob (`IDENTITY_TRUST_MODE = network | signed`) and verifies `X-Identity-Signature`. Same middleware, same header schema, no protocol change.

`X-Identity-Source` lets the salesagent know which host forwarded the request (e.g., `scope3`, `acme-storefront`). Useful for audit logs and for cases where one salesagent instance might serve more than one host product.

No salesagent-side `User` records for embedded tenants. Identity is ephemeral — every request re-reads the headers. The audit log captures email/org/user-id from the headers for traceability.

The salesagent's existing OIDC blueprint (`src/admin/blueprints/oidc.py`) is unused on embedded instances; per-tenant OIDC config is for open instances.

**Org-picker, multi-org users**: handled entirely by the host before the request reaches the salesagent. By the time the proxy forwards, one org is selected. Salesagent never sees ambiguity.

**Listener hardening on embedded instances** (required, not optional, since trust is network-based):
- Bind to a private interface only — never `0.0.0.0`.
- Allow-list the host's proxy source IP/range at the salesagent's listener.
- Reject any request missing the required `X-Identity-*` headers — fail closed.
- Audit-log the headers on every request for post-hoc detection.

**Super-admin backdoor**: salesagent staff log into the salesagent's direct URL (private network only — VPN or office IP) using Google OAuth restricted to `SUPER_ADMIN_EMAILS`. This bypass exists for ops, debugging, incident response. It does not depend on the host's identity contract and works regardless of host availability.

### 3. Reverse proxy

`<host>/<host-prefix>/salesagent/{tenant_id}/...` → `salesagent.internal/tenant/{tenant_id}/...`. The host's edge proxy forwards; salesagent serves HTML. (For the Scope3 reference deployment: `scope3.com/storefront/salesagent/{tenant_id}/...`.)

The salesagent already supports path-prefix mounts (CLAUDE.md pattern #6: `request.script_root` in Python, `scriptRoot` in JS). Running behind any host's proxy is the same shape with a different prefix. Required:

- `ProxyFix` middleware for `X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Forwarded-Prefix`.
- All template URLs and JS fetch calls already use `script_root` — verify no hardcoded URLs slipped in.
- Cookies are not used for embedded-tenant auth (the identity headers are the credential). No cross-domain cookie problems.

iframe was an alternative — rejected. CSP, X-Frame-Options, history/deep-linking pain. Reverse proxy is materially better and the codebase is already shaped for it.

### 4. Subdomain routing

Closed for embedded instances. Tenant identity comes from the URL path (`/tenant/{tenant_id}/...`) and the `X-Identity-Org-Id` header, validated to match. `src/core/domain_config.py` and the approximated.app integration are unused on embedded instances. Open instances keep them. This is a major simplification of the routing layer for any embedded deployment.

### 5. Comprehensive Tenant Management API

Extend the existing `/api/v1/tenant-management` blueprint into a complete configuration surface. Every knob a publisher can set in today's UI must be settable via the API. The salesagent does not pre-decide what's "advanced" or "exposable" — the host chooses what to expose to its users.

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

The marquee endpoint is `POST /tenants/provision`. It bundles tenant creation + adapter configuration + initial principal + initial products + token issuance + `external_org_id` stamping into one call. Returns tenant ID, MCP URL, A2A URL, principal API tokens, and the embed URL the host product should link to.

Endpoints removed from this list compared to v1 of the proposal:
- `/oidc-config` — embedded instances use the host's SSO, not per-tenant OIDC.
- `/users` — the host owns publisher-side users; no salesagent User records for embedded tenants.
- `/domains` — no per-tenant subdomains in embedded mode.
- `/favicon`, `/slack/test` and similar UI-utility endpoints — operations console reads them via the read endpoints; mutations not needed in embedded mode.

### 6. OpenAPI from day one

Adopt **`spectree`**: Pydantic request/response models per endpoint, served at `/openapi.json` with Swagger UI at `/docs`.

- Repo already lives on Pydantic; spectree fits.
- Stays in Flask — no FastAPI migration for one slice.
- Forces request/response schema discipline.
- Any host generates a typed client from the spec.

Alternatives rejected: hand-written `openapi.yaml` (drifts from code); FastAPI for this slice (overkill, two web frameworks).

### 7. UI behavior on embedded instances

Middleware checks `is_embedded` on every tenant-scoped request. The UI is *not* fully read-only — it's *bounded* to the publisher-managed scope.

**Platform-managed pages** (settings → general, settings → adapter, settings → domains, settings → OIDC, account/billing, tenant lifecycle):
- Render read-only with a banner naming the host, e.g.: *"Platform settings managed by {host product name}."* The banner reads `tenant.external_source` (rendered through a display-name filter) so the host name is parametric, not hardcoded.
- Nav entries hidden by default; visible only to super-admin backdoor users for debugging.
- Mutation routes return 403 even if reached directly via URL (the model-layer guard catches anything that slips through middleware).

**Publisher-managed pages** (products, principals, creatives, workflows, properties, inventory profiles, agents, slack, business rules, policy):
- Fully writable through the UI. No banner, no friction.
- Same routes as today's open-instance UI. No code path divergence beyond the platform-page hiding.

**Operational pages** (dashboard, media-buy viewer, audit log, sync status):
- Always read-only by nature. Identical for embedded and open tenants.

The super-admin backdoor sees all tenants without restriction — embedded mode does not block salesagent staff. They can still write to platform-managed surfaces directly via super-admin tools (the model guard checks for the Tenant Management API session flag *or* a super-admin escape flag).

### 8. Network surface (embedded instance)

When `MANAGED_INSTANCE=true`:

- Buyer protocol endpoints (`/mcp/`, `/a2a`) accept embedded traffic from the host product's buyer agent. Callers identify the buyer agent and tenant via `X-Principal-Id` plus the same `X-Identity-*` header contract used for the UI proxy. `BUYER_PROTOCOL_ALLOWED_CIDRS` may be set to add a direct source-IP allow-list; when it is unset, the deployment relies on the host proxy/firewall to keep the salesagent private and the service logs a warning.
- Tenant Management API (`/api/v1/tenant-management`) same network restriction. API key required (the one credential that crosses the boundary on purpose, identifying the host's control plane).
- Salesagent admin URL same network restriction (super-admin backdoor reachable via VPN or internal hostname only).
- Reverse-proxied UI traffic from the host product comes through the host's network — also private.

Net result: embedded deployments are expected to keep the salesagent off the public internet, and zero per-principal credentials need to be passed around. Identity flows as headers; trust is established by the host proxy/firewall, with optional CIDR enforcement as defense in depth for buyer-protocol traffic.

**Open-instance behavior is unchanged.** Public MCP/A2A keeps `x-adcp-auth` bearer tokens per principal. The mode is selected by `MANAGED_INSTANCE`; the salesagent's `resolve_identity()` branches on it.

> **Naming note.** The env var is still `MANAGED_INSTANCE` for backwards compatibility with existing deployments. New code references should treat it as the embedded-instance switch; renaming to `EMBEDDED_INSTANCE` is a separate cutover.

### 9. Webhooks (optional, post-v1)

For the host product to surface live state without polling, add outbound webhooks: workflow created/approved/rejected, sync failed, media buy delivered, adapter connection lost. Signed payloads, at-least-once with retry. Not required for v1 — polling `GET /status` and `GET /workflows` is sufficient.

## Status

The embedded-mode launch path has shipped. The Tenant Management API, identity propagation, network policy enforcement, UI hardening, workflow approve/reject mutations, drill-down read endpoints, outbound webhooks, buyer-advertiser routing, the GAM advertisers cache, and the IA cleanup (Tenant Settings vs Configure decomposition with instance-level `EMBEDDED_CAPABILITIES` flags) are all live. Publisher-managed CRUD via API (advertisers / products / sub-resource bulk endpoints) remains deferred — publishers manage these via the proxied UI and there's been no concrete need for programmatic access.

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

If the optional publisher-managed sprints (4–5) ship, the publisher-managed UI handlers naturally converge to shared business logic alongside their API endpoints. Until then, only platform-managed surfaces (sprints 1, 1.5) and operational surfaces (sprint 3) get the extraction. The non-embedded-only surfaces (open-instance customer config, etc.) can stay inline indefinitely — allowlisted in the structural-guard FIXME registry and shrink over time.

**Why not refactor everything first:** would block API delivery on a multi-sprint refactor that ships zero value to the host product until the end.

## Open questions

1. **Identity contract canonical doc location.** The `X-Identity-*` header schema is owned by the salesagent and consumed by host products. Should it live as a top-level integration spec (e.g., `docs/integration/embedded-mode-identity-contract.md`) referenced from this design, so host integrators can read it without wading through the design rationale?
2. **One external org → one salesagent tenant.** True for now, but design should leave room — if a publisher has multiple GAM networks, they may want multiple tenants under one org. Probably modeled as `Tenant.external_org_id` (non-unique, with a "primary" tenant for ambiguous routes) but defer until needed.
3. **Conflict resolution.** What happens if a super-admin manually edits an embedded tenant's DB row directly? Hard-block at the model layer (`Tenant.save()` rejects writes when `is_embedded=true` and caller isn't the Tenant Management API), or trust the middleware? Recommend hard-block — middleware drift is a class of bug not worth tolerating.
4. **Audit trail for embedded tenants.** With no User table, every mutation is "by host control plane" or "by upstream user X (email Y, org Z)". Schema change to `AuditLog`: optional `external_user_email`, `external_user_id`, `external_org_id`, `external_source`.
5. **Migration strategy for existing tenants.** If you decide later to migrate a direct-customer tenant to embedded mode, what's the cutover? Probably: `PATCH /tenants/{id}` with `is_embedded=true` + `external_org_id`, then forward future writes through the host. Document but don't build tooling in v1.

## Risks

- **Two control planes during transition.** Until middleware ships in sprint 2, both the host product and any direct admin-UI access can write to embedded tenants on platform-managed surfaces. Mitigation: the model-layer write guard ships in sprint 1, providing a hard backstop even before middleware lands.
- **API surface drift.** As the UI evolves for non-embedded tenants on open instances, new config knobs may not get API endpoints. Mitigation: structural guard requiring every new field on a config model to have a corresponding API endpoint or an explicit `embedded_unsupported=true` annotation.
- **Reverse-proxy URL leakage.** If any salesagent template hardcodes URLs, they'll point at the wrong host when proxied. Mitigation: audit templates and JS for hardcoded paths in sprint 1; the `script_root` pattern is already enforced but not in tests.
- **Super-admin lockout.** If the salesagent's private network connectivity breaks, ops can't reach the backdoor either. Mitigation: ensure the backdoor URL is reachable via at least two paths (VPN + bastion).

## Decision

Pending. Next step if accepted: detailed Pydantic schema spec for `POST /tenants/provision` and the JWT verification design.
