# Sprint 2 Spec: Embedded-Mode Hardening — UI, Network, Buyer-Protocol Auth

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1](./embedded-mode-sprint-1.md), [sprint 1.5](./embedded-mode-sprint-1.5.md)
**Status:** Draft
**Last updated:** 2026-05-04

> **Reference deployment.** Concrete examples in this doc cite Scope3 Storefront as the first reference deployment. The deliverables are generic — any host product embedding PSA uses the same surface.

## Scope

Sprints 1 + 1.5 built the platform-managed API surface and the host-integration essentials (preview, status, identity contract). Sprint 2 makes the runtime actually behave correctly in embedded mode:

1. **UI middleware** — scopes nav, hides platform-managed config pages, renders read-only banners. Publisher-managed pages remain fully writable.
2. **Identity-propagation request scoping** — the `X-Identity-*` header reader (built in sprint 1) gets wired into the request lifecycle so every UI request on an embedded instance is identity-scoped. Audit log captures it.
3. **Network policy enforcement** — when `MANAGED_INSTANCE=true`, every surface (MCP/A2A, Tenant Management API, admin UI direct URL) rejects traffic outside the configured private CIDR.
4. **`resolve_identity()` for MCP/A2A in embedded mode** — header-based principal/tenant scoping, no bearer tokens. Open-instance behavior unchanged.

After sprint 2, an embedded-mode salesagent is fully operational end-to-end. No public exposure, no per-principal tokens, all writes either go through the Tenant Management API (platform-managed) or through the proxied UI scoped by upstream identity (publisher-managed).

Out of scope for sprint 2 (deferred to sprints 3+):
- Workflow approve/reject + remaining read-only operational endpoints (workflow detail, media-buys, audit-log, sync-history) — sprint 3
- Publisher-managed CRUD via API (principals, products, etc.) — sprints 4–5 (optional)
- Outbound webhooks — sprint 6
- Periodic background re-verification of authorized properties — sprint 3+

## Components

### 1. UI middleware: `embedded_tenant_middleware`

Lives in `src/admin/middleware/embedded_tenant.py`. Registered as a Flask `before_request` hook on the admin app.

**Responsibilities:**

```python
def embedded_tenant_middleware():
    """
    Runs before every admin route. Determines whether the current request
    is on an embedded tenant, and if so, applies the platform-managed scoping rules.

    Sets g.embedded_context with:
      - is_managed: bool
      - external_identity: PropagatedIdentity | None  (from X-Identity-* headers)
      - is_super_admin_override: bool                  (set by super-admin backdoor)
      - tenant_id: str | None                          (resolved from URL or identity)
    """
```

**Decision tree per request:**

1. Resolve tenant from URL path (`/tenant/<tenant_id>/...`).
2. If tenant doesn't exist or isn't managed: pass through (open-instance behavior).
3. If `MANAGED_INSTANCE=true` AND tenant is managed:
   a. Check for super-admin override (`SUPER_ADMIN_EMAILS` Google OAuth session). If present, allow everything; set `is_super_admin_override=true`.
   b. Else: read `X-Identity-*` headers via the sprint-1 reader. If absent: 403 `identity_required` (fail closed).
   c. Validate the identity's `X-Identity-Org-Id` matches the tenant's `external_org_id`. If not: 403 `identity_org_mismatch`.
   d. Map `X-Identity-Role` → salesagent role; store on `g`.
4. For routes flagged as platform-managed (registered via decorator, see below):
   - GET: render the page read-only with the banner.
   - Mutating methods (POST, PUT, PATCH, DELETE): return 403 `platform_managed_route` unless `is_super_admin_override`.
5. For routes not flagged: pass through (publisher-managed; UI handles authz internally based on role).

**Route classification decorator:**

```python
def platform_managed(view_func):
    """Mark a Flask route as platform-managed in embedded mode (read-only for publishers)."""
    view_func._platform_managed = True
    return view_func

# Applied to existing routes:
# - settings_bp /general (POST), /adapter (POST), /domains/*, /emails/*, /access/*
# - tenants_bp /<tid>/update, /<tid>/deactivate, /<tid>/upload_favicon, /<tid>/update_favicon_url
# - oidc_bp all routes
# - core_bp /create_tenant, /admin/tenant/<tid>/reactivate
```

The middleware reads `view_func._platform_managed` at request time; routes without the decorator are publisher-managed by default.

**Why decorator-based, not URL-pattern matching:** the route's owner declares the classification at the route level. Adding new platform-managed routes is a one-line annotation, not a separate config file that drifts.

### 2. Banner + nav scoping

**Banner**: a Jinja macro in `templates/components/managed_banner.html`:

```jinja
{% if g.embedded_context and g.embedded_context.is_managed and not g.embedded_context.is_super_admin_override %}
  <div class="banner banner--managed">
    Platform settings are managed by {{ g.embedded_context.external_identity.source | source_display_name }}.
    To change these, edit them in {{ g.embedded_context.external_identity.source | source_display_name }}.
  </div>
{% endif %}
```

Rendered at the top of every platform-managed page. `source_display_name` filter maps `scope3` → `Scope3 Storefront`, etc.

**Nav scoping**: existing nav templates call a helper:

```python
def visible_nav_items(role, embedded_context) -> list[NavItem]:
    """Filter the nav based on managed status and role."""
    items = ALL_NAV_ITEMS
    if embedded_context.is_managed and not embedded_context.is_super_admin_override:
        items = [i for i in items if not i.platform_managed]
    items = [i for i in items if role >= i.required_role]
    return items
```

Items get `platform_managed=True` in the registry — same classification as routes. The "Settings" parent item is dropped entirely if all its children are platform-managed; otherwise only the platform sub-items are filtered.

### 3. Identity-propagation reader (sprint 1) wired in

Sprint 1 shipped the `read_identity_from_request()` reader as a function. Sprint 2 wires it into the request lifecycle:

- `embedded_tenant_middleware` calls it, sets `g.embedded_context.external_identity`.
- Audit log decorator (`src/admin/utils/audit_decorator.py`) reads `g.embedded_context.external_identity` and populates the new `external_*` columns on `AuditLog` rows.
- Existing user-resolution code (`src/admin/auth_helpers.py`) returns the propagated identity when `is_managed && !is_super_admin_override`, so existing handlers don't need to know whether they're running in embedded mode — they just see "current user" with role.

### 4. `resolve_identity()` for MCP/A2A in embedded mode

Today's `resolve_identity()` (in `src/core/auth.py` or similar) reads `x-adcp-auth` bearer token, looks up the principal by token, returns a `ResolvedIdentity` with tenant + principal scope.

**Sprint 2 change:**

```python
def resolve_identity(headers: Headers, *, protocol: Literal["mcp", "a2a"]) -> ResolvedIdentity:
    if config.MANAGED_INSTANCE:
        return _resolve_identity_managed(headers, protocol=protocol)
    return _resolve_identity_open(headers, protocol=protocol)

def _resolve_identity_managed(headers, *, protocol) -> ResolvedIdentity:
    """
    Embedded mode: trust is network-based, no bearer tokens.
    Caller specifies tenant + principal via headers.
    """
    tenant_id = headers.get("X-Tenant-Id")
    principal_id = headers.get("X-Principal-Id")
    if not tenant_id or not principal_id:
        raise IdentityRequiredError("Embedded mode requires X-Tenant-Id and X-Principal-Id headers")

    tenant = TenantRepository.get(tenant_id)
    if not tenant or not tenant.is_embedded:
        raise InvalidTenantError(tenant_id)

    principal = PrincipalRepository.get(tenant_id, principal_id)
    if not principal:
        raise InvalidPrincipalError(principal_id)

    return ResolvedIdentity(
        tenant=tenant,
        principal=principal,
        source="managed_header",
        # Optional upstream-identity context for audit:
        external_email=headers.get("X-Identity-Email"),
        external_source=headers.get("X-Identity-Source"),
    )

def _resolve_identity_open(headers, *, protocol) -> ResolvedIdentity:
    """Existing bearer-token flow. Unchanged."""
    token = headers.get("x-adcp-auth")
    ...
```

**Why**: open-instance behavior is unchanged — same auth path that exists today. Embedded mode opts out of bearer tokens entirely; trust is network-based per the parent design.

The branch is at the entry point; all downstream code (tools, repositories, etc.) operates on `ResolvedIdentity` regardless of how it was resolved. No changes needed below `resolve_identity()`.

### 5. Network policy enforcement

**Application-level guard** in addition to whatever the deployment's firewall/load-balancer does. Belt-and-suspenders.

`src/core/middleware/network_policy.py`:

```python
def network_policy_middleware(allowed_cidrs: list[str], surface_name: str):
    """
    Reject requests whose source IP isn't in any of the allowed CIDRs.

    Reads the real client IP from X-Forwarded-For (last hop) when behind a trusted proxy.
    Falls back to remote_addr.
    """
    networks = [ipaddress.ip_network(c) for c in allowed_cidrs]

    def check():
        if not config.MANAGED_INSTANCE:
            return  # Open instance: no network restriction.
        client_ip = _resolve_client_ip(request, trusted_proxies=config.TRUSTED_PROXY_CIDRS)
        if not any(ipaddress.ip_address(client_ip) in net for net in networks):
            audit_log.warning(f"network_policy_denied surface={surface_name} ip={client_ip}")
            abort(403, description="network_policy_denied")

    return check
```

**Configured per surface** via env vars:

| Surface | Env var |
|---|---|
| MCP/A2A | `BUYER_PROTOCOL_ALLOWED_CIDRS` |
| Tenant Management API | `MANAGEMENT_API_ALLOWED_CIDRS` |
| Admin UI direct (super-admin backdoor) | `ADMIN_UI_ALLOWED_CIDRS` (typically VPN range) |

If a surface's env var is unset on an embedded instance, the salesagent **fails to start** with a configuration error. Forces deployers to make a deliberate choice — never silent "allow all" defaults.

**Trusted-proxy handling**: `TRUSTED_PROXY_CIDRS` lists the host product's upstream proxy ranges (e.g., the host's nginx in front of the salesagent). Requests from those IPs have their `X-Forwarded-For` honored; everything else uses `remote_addr`. Standard pattern — `werkzeug.middleware.proxy_fix.ProxyFix` configured at app init also relies on this.

**Listener binding**: the salesagent's gunicorn/uvicorn must bind to a private interface only. Documented in deployment guide. Not enforced by app code (it's a deployment concern), but the startup script can refuse to bind `0.0.0.0` when `MANAGED_INSTANCE=true` is set — small belt-and-suspenders.

### 6. Super-admin backdoor

Salesagent staff log in via Google OAuth (existing flow), email allowlisted via `SUPER_ADMIN_EMAILS`. On embedded instances:
- Reachable via private network only (`ADMIN_UI_ALLOWED_CIDRS` includes the VPN/bastion range).
- Sets `g.embedded_context.is_super_admin_override = true` in the middleware.
- Bypasses platform-managed-route restrictions, but each mutation is **audit-logged with the super-admin email and "override" reason**.
- Bypasses the model-layer write guard via `session.info["super_admin_override"] = True` (set by an `@super_admin_override` decorator applied to relevant emergency routes).

The override exists for incident response — never the routine path. Audit-log queries should be set up to alert on heavy super-admin override usage so it doesn't silently become normal.

## Audit log integration

The new `external_*` columns on `AuditLog` (added in sprint 1) get populated as follows:

| Caller path | external_user_email | external_user_id | external_org_id | external_source |
|---|---|---|---|---|
| UI request from host product's proxy | `X-Identity-Email` | `X-Identity-User-Id` | `X-Identity-Org-Id` | `X-Identity-Source` |
| Tenant Management API call | null | null | null | `"management_api"` |
| MCP/A2A call (embedded mode) | `X-Identity-Email` if present | `X-Identity-User-Id` if present | `X-Identity-Org-Id` if present | `X-Identity-Source` or `"managed_header"` |
| Super-admin override | super-admin email (Google) | null | null | `"super_admin_override"` |
| Open-instance request | null (existing User row used instead) | null | null | null |

Audit log queries can filter on `external_source` to slice by traffic origin. Useful for "show me everything the host control plane changed today" or "show me all super-admin override events."

## Pydantic schemas

Sprint 2 is mostly middleware/runtime, not API endpoints. The only new schema is:

```python
class NetworkPolicyDeniedError(BaseModel):
    error: Literal["network_policy_denied"] = "network_policy_denied"
    surface: str          # which surface rejected — for ops debugging
    requested_at: datetime

class IdentityRequiredError(BaseModel):
    error: Literal["identity_required"] = "identity_required"
    surface: str
    missing_headers: list[str]
```

Returned as 403 responses.

## Acceptance criteria

**UI middleware:**
- [ ] Routes annotated `@platform_managed` return 403 on POST/PUT/PATCH/DELETE for non-super-admin users on embedded tenants.
- [ ] Same routes return 200 with read-only template + banner on GET.
- [ ] Routes without the annotation work normally (publisher-managed paths unaffected).
- [ ] Nav helper hides platform-managed items for embedded tenants; "Settings" parent dropped if all children hidden.
- [ ] Banner renders only on embedded-tenant pages, never on non-embedded or super-admin-override sessions.

**Identity propagation:**
- [ ] Request to an embedded-tenant URL without `X-Identity-*` headers returns 403 `identity_required`.
- [ ] Request with `X-Identity-Org-Id` not matching the URL's tenant returns 403 `identity_org_mismatch`.
- [ ] Audit log entries on embedded-tenant mutations carry the external_* fields populated from headers.
- [ ] Audit log entries via Tenant Management API carry `external_source="management_api"`.

**MCP/A2A embedded-mode resolve_identity:**
- [ ] Call to MCP with `X-Tenant-Id` + `X-Principal-Id` resolves correctly; no bearer token required or accepted.
- [ ] Call to MCP without those headers returns the protocol's auth-error response (e.g., MCP error -32001).
- [ ] Call to MCP with bearer token `x-adcp-auth` on an embedded instance is **ignored** (not a fallback) — embedded mode rejects the call same as no headers.
- [ ] On `MANAGED_INSTANCE=false`, existing bearer-token flow works unchanged (regression test).

**Network policy:**
- [ ] On `MANAGED_INSTANCE=true` with `BUYER_PROTOCOL_ALLOWED_CIDRS=10.0.0.0/8`, request from `192.168.1.1` to `/mcp/` returns 403 `network_policy_denied`.
- [ ] Same setup, request from `10.1.2.3` succeeds.
- [ ] On `MANAGED_INSTANCE=true` with no `BUYER_PROTOCOL_ALLOWED_CIDRS` set, the salesagent fails to start with a configuration error.
- [ ] On `MANAGED_INSTANCE=false`, no CIDR check occurs.
- [ ] `X-Forwarded-For` is honored only when source IP is in `TRUSTED_PROXY_CIDRS`.

**Super-admin override:**
- [ ] Logged-in super admin accessing platform-managed route on embedded tenant: succeeds.
- [ ] Each super-admin override mutation creates an audit log entry with `external_source="super_admin_override"`.
- [ ] Non-super-admin Google session attempting same: 403.

**Integration:**
- [ ] End-to-end: provision an embedded tenant via API; visit the proxied UI as a host-product user; products page is editable; tenant settings page is read-only with banner; attempt POST to settings update → 403; super-admin login bypass works.
- [ ] End-to-end: host's buyer agent calls `/mcp/get_products` with `X-Tenant-Id`/`X-Principal-Id` → succeeds. Same call from public IP → 403. Same call without headers → auth error.

**Regressions:**
- [ ] Open-instance integration tests pass unchanged on `MANAGED_INSTANCE=false`.
- [ ] Existing principal token-based MCP/A2A auth works on `MANAGED_INSTANCE=false`.

## Open questions

1. **Per-tenant role mapping config.** `X-Identity-Role` values come from host products with their own role taxonomies. Today: hardcode `admin | member | viewer` mapping. Future: per-tenant or per-source role config tables. Defer until a host hits multi-role needs.
2. **`X-Forwarded-For` chain trust.** With the host product's proxy in front of our network policy middleware, the chain is `client → host nginx → salesagent`. We trust the last hop (host's nginx IP in `TRUSTED_PROXY_CIDRS`). Confirm the host's nginx is the only hop between the public internet and us; if there's an L7 LB in between, its IP needs to be in the trust list too.
3. **Failure mode if model-layer guard trips for a non-platform-managed write.** The guard is built per sprint 1 to fire only on platform-managed columns/tables. If it fires unexpectedly (someone added a new platform-managed table without updating the guard), the user sees a 500. Worth logging this case prominently and perhaps adding a structural guard that catches "platform-managed tables not in the write-guard registry."
4. **MCP/A2A response when `MANAGED_INSTANCE=true` but headers missing.** Should this be a network-policy 403 (request shouldn't have arrived) or an MCP-spec auth error? Probably MCP-spec auth error to keep buyer-agent error handling consistent across modes; document in the buyer-protocol auth docs.
5. **Listener binding enforcement.** The "fail to start if `MANAGED_INSTANCE=true` and binding to 0.0.0.0" check requires reading the WSGI server config — easy with gunicorn args, harder with uvicorn config files. Decide whether this is worth doing in app code or just documenting as a deployment requirement.

## What sprints 5+ build on this

Sprint 2 closes out the runtime hardening. After sprints 1, 1.5, and 2, an embedded-mode salesagent is fully usable end-to-end:
- API automation surface for everything the host product cares about (sprint 1 + 1.5).
- Runtime safety — UI middleware, network policy, embedded-mode buyer-protocol auth (sprint 2).
- Host has tenant status visibility (`GET /status` from sprint 1.5).

After sprint 2:
- **Sprint 3**: workflow approve/reject + remaining read-only operations (workflow detail, media-buys, audit-log, sync-history). Detail views to back the status summary; operational mutations the host wants surfaced in its UI.
- **Sprint 4 (optional)**: publisher-managed CRUD via API (principals, products) — automation conveniences. Publishers also do these via the proxied UI.
- **Sprint 5 (optional)**: remaining publisher-managed sub-resources (tags, properties, profiles, etc.) via API.
- **Sprint 6 (optional)**: outbound webhooks for state changes. Replaces polling `GET /status`.
