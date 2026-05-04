# Sprint 1 Spec: Managed Tenant Mode Foundation

**Parent design:** [embedded-mode](./embedded-mode.md)
**Status:** Draft
**Last updated:** 2026-05-04

## Scope

Sprint 1 delivers the **complete platform-managed surface** Scope3 needs to fully manage tenants via API:

1. Schema migration for embedded mode (`is_embedded`, `external_org_id`, `external_source` on `Tenant`; `external_*` fields on `AuditLog`).
2. Identity-propagation middleware (reads `X-Identity-*` headers; sprint 1 ships the reader only — request scoping lands in sprint 4).
3. spectree wired up; `GET /api/v1/tenant-management/openapi.json` and `/docs` (Swagger UI) live.
4. **Tenant lifecycle endpoints** — full CRUD over the platform-managed scope:
   - `POST /tenants/provision` (marquee one-shot)
   - `GET /tenants` (list)
   - `GET /tenants/{id}` (read)
   - `PATCH /tenants/{id}` (update — platform-managed fields only)
   - `POST /tenants/{id}/deactivate`
   - `POST /tenants/{id}/reactivate`
   - `DELETE /tenants/{id}`
5. **Adapter management endpoints**:
   - `GET /tenants/{id}/adapter-config`
   - `PUT /tenants/{id}/adapter-config`
   - `POST /tenants/{id}/adapter-config/test-connection`
6. Reverse-proxy compatibility verified — salesagent works under a Scope3 path prefix.
7. Scoped write guard at the model layer (platform-managed columns/tables only — see "Scoped write guard" section below).

After sprint 1, Scope3 can fully provision, configure, update, deactivate, and delete tenants; it can manage adapter credentials and test connections — all via API with a published OpenAPI spec.

Not in sprint 1:
- Consolidated `GET /status` endpoint + adapter preview — [sprint 1.5](./embedded-mode-sprint-1.5.md).
- UI hardening, banner / nav hiding, network-policy lockdown for `MANAGED_INSTANCE`, MCP/A2A embedded-mode `resolve_identity()` — [sprint 2](./managed-tenant-mode-sprint-2.md).
- Workflow approve/reject + drill-down read endpoints (workflows list/detail, media-buys, audit-log, sync-history) — [sprint 3](./managed-tenant-mode-sprint-3.md).
- Publisher-managed CRUD via API (principals, products, sub-resources) — sprints 4–5 (optional).
- Outbound webhooks — sprint 6 (optional).

## Database changes

### Migration: `add_managed_mode_to_tenant`

```python
def upgrade():
    op.add_column("tenants", sa.Column("is_embedded", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tenants", sa.Column("external_org_id", sa.String(255), nullable=True))
    op.add_column("tenants", sa.Column("external_source", sa.String(64), nullable=True))
    op.create_index("ix_tenants_external_org_id", "tenants", ["external_org_id"])
    # Not unique — see proposal open question 2 (multi-tenant per org may come later).

def downgrade():
    op.drop_index("ix_tenants_external_org_id", "tenants")
    op.drop_column("tenants", "external_source")
    op.drop_column("tenants", "external_org_id")
    op.drop_column("tenants", "is_embedded")
```

### Migration: `add_external_identity_to_audit_log`

```python
def upgrade():
    op.add_column("audit_logs", sa.Column("external_user_email", sa.String(255), nullable=True))
    op.add_column("audit_logs", sa.Column("external_user_id", sa.String(255), nullable=True))
    op.add_column("audit_logs", sa.Column("external_org_id", sa.String(255), nullable=True))
    op.add_column("audit_logs", sa.Column("external_source", sa.String(64), nullable=True))

def downgrade():
    op.drop_column("audit_logs", "external_source")
    op.drop_column("audit_logs", "external_org_id")
    op.drop_column("audit_logs", "external_user_id")
    op.drop_column("audit_logs", "external_user_email")
```

### Scoped write guard for platform-managed columns/tables

The boundary is *infrastructure vs. business* (see [parent design § 1a](./embedded-mode.md#1a-platform-managed-vs-publisher-managed-surfaces)). Only platform-managed surfaces are locked; publisher-managed surfaces (products, principals, creatives, workflows, etc.) remain writable from the UI.

```python
# src/core/database/managed_tenant_guard.py
from sqlalchemy import event
from sqlalchemy.orm.attributes import get_history
from src.core.database.models import Tenant, AdapterConfig

# Per-table allow-list of mutable fields when is_embedded=true.
# Anything NOT in this list (or any table not listed here) is platform-managed
# and requires the management API session flag.
PUBLISHER_WRITABLE_FIELDS: dict[type, set[str]] = {
    # Tenant columns the publisher can never touch in embedded mode are platform-managed.
    # Today's Tenant model has no publisher-writable fields — name/billing/active/external_*
    # are all platform concerns. Listed empty for clarity.
    Tenant: set(),
    # AdapterConfig is fully platform-managed.
    AdapterConfig: set(),
    # Products, Principals, Creatives, Workflows, etc. are NOT in this dict at all,
    # which means the guard doesn't fire on them — they're publisher-managed by default.
}

class ManagedTenantWriteError(Exception):
    pass

def _caller_is_authorized(connection) -> bool:
    info = connection.info
    return bool(info.get("management_api_caller") or info.get("super_admin_override"))

@event.listens_for(Tenant, "before_update")
@event.listens_for(AdapterConfig, "before_update")
def block_platform_managed_writes(mapper, connection, target):
    # Resolve the parent tenant
    tenant_id = target.tenant_id if hasattr(target, "tenant_id") else target.tenant_id
    # In practice, Tenant.tenant_id IS the tenant; AdapterConfig has FK
    if isinstance(target, Tenant):
        if not target.is_embedded:
            return
    else:
        # Look up the parent tenant's managed flag via the same connection
        parent_managed = connection.execute(
            select(Tenant.is_embedded).where(Tenant.tenant_id == tenant_id)
        ).scalar()
        if not parent_managed:
            return

    if _caller_is_authorized(connection):
        return

    # For Tenant updates, check whether any changed fields are publisher-writable.
    # If every changed field is in PUBLISHER_WRITABLE_FIELDS, allow it.
    writable = PUBLISHER_WRITABLE_FIELDS.get(type(target), set())
    changed_fields = {col.key for col in mapper.attrs if get_history(target, col.key).has_changes()}
    if changed_fields and changed_fields.issubset(writable):
        return

    raise ManagedTenantWriteError(
        f"{type(target).__name__} for tenant {tenant_id} is platform-managed; "
        f"changes to {sorted(changed_fields)} must go through the Tenant Management API."
    )

@event.listens_for(Tenant, "before_insert")
@event.listens_for(AdapterConfig, "before_insert")
def block_platform_managed_inserts(mapper, connection, target):
    # Inserts into platform-managed tables for managed tenants must come from the API.
    if isinstance(target, Tenant) and not target.is_embedded:
        return
    if not _caller_is_authorized(connection):
        raise ManagedTenantWriteError(
            f"Inserting {type(target).__name__} for a managed tenant requires the Tenant Management API."
        )
```

The Tenant Management API endpoints set `session.info["management_api_caller"] = True` on entry. The super-admin backdoor sets `session.info["super_admin_override"] = True` for emergencies. UI handlers and other code paths don't set either flag — writes to platform-managed surfaces fail loudly.

Critically: writes to publisher-managed tables (Product, Principal, Creative, Workflow, Property, etc.) are **not** intercepted by this guard. Their existing UI handlers continue to work for managed tenants without any code change.

Sprint 1 ships the guard wired up for `Tenant` and `AdapterConfig` only. Other platform-managed surfaces (domain config, OIDC config) are added to the guard list as their tables are touched in later sprints, but they're not commonly written-to in embedded mode anyway.

## Pydantic schemas

Lives in `src/admin/api_schemas/tenant_management.py` (new file). All schemas extend `BaseModel` with `extra="forbid"` in dev/CI per CLAUDE.md pattern #7.

### Adapter config (discriminated union)

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field, SecretStr

class GAMAdapterConfig(BaseModel):
    type: Literal["google_ad_manager"] = "google_ad_manager"
    network_code: str = Field(..., min_length=1, max_length=32)
    service_account_email: str
    service_account_key_json: SecretStr  # full JSON of the service account key
    refresh_token: SecretStr | None = None  # if using OAuth instead of SA key

class MockAdapterConfig(BaseModel):
    type: Literal["mock"] = "mock"
    # Mock takes no config

# Add Kevel/Triton/Broadstreet later — sprint 1 ships GAM + Mock.

AdapterConfig = Annotated[
    GAMAdapterConfig | MockAdapterConfig,
    Field(discriminator="type"),
]
```

### Provision request

```python
class InitialPrincipalRequest(BaseModel):
    """Optional initial advertiser created at provision time."""
    name: str = Field(..., min_length=1, max_length=255)
    external_advertiser_id: str | None = None  # GAM advertiser ID, etc.

class ProvisionTenantRequest(BaseModel):
    # Identity (required)
    name: str = Field(..., min_length=1, max_length=255)
    external_org_id: str = Field(..., min_length=1, max_length=255)
    external_source: str = Field(..., min_length=1, max_length=64)  # e.g. "scope3"
    contact_email: EmailStr

    # Adapter (required — a tenant without an adapter is useless)
    adapter: AdapterConfig

    # Defaults (optional, sensible defaults applied)
    default_currency: str = Field("USD", min_length=3, max_length=3)
    billing_plan: str = Field("standard", max_length=64)

    # Optional convenience: create one principal in the same call
    initial_principal: InitialPrincipalRequest | None = None
```

### Provision response

```python
class ProvisionedPrincipalResponse(BaseModel):
    principal_id: str
    name: str
    # No api_token — buyer-protocol auth in embedded mode flows through the
    # identity-propagation contract, not per-principal tokens. See sprint 2 § Auth boundary.

class AdapterStatusResponse(BaseModel):
    type: str
    configured: bool
    connection_test_passed: bool
    connection_test_error: str | None = None

class ProvisionTenantResponse(BaseModel):
    tenant_id: str
    name: str
    external_org_id: str
    external_source: str
    is_embedded: Literal[True]
    created_at: datetime

    # Surfaces — URLs Scope3 needs to know
    mcp_url: str        # e.g. https://salesagent.internal/mcp/
    a2a_url: str        # e.g. https://salesagent.internal/a2a
    admin_url_path: str # path Scope3 mounts under storefront, e.g. /tenant/{tenant_id}

    # Adapter status
    adapter: AdapterStatusResponse

    # Set only if initial_principal was in the request
    initial_principal: ProvisionedPrincipalResponse | None = None
```

### Tenant lifecycle schemas

```python
class TenantSummary(BaseModel):
    tenant_id: str
    name: str
    external_org_id: str | None
    external_source: str | None
    is_embedded: bool
    is_active: bool
    billing_plan: str
    ad_server: str | None
    adapter_configured: bool
    created_at: datetime

class TenantDetail(TenantSummary):
    contact_email: EmailStr
    default_currency: str
    # Plus any other platform-managed fields exposed for read

class ListTenantsResponse(BaseModel):
    tenants: list[TenantSummary]
    count: int

class UpdateTenantRequest(BaseModel):
    """PATCH — all fields optional. Only platform-managed fields exposed here."""
    name: str | None = Field(None, min_length=1, max_length=255)
    contact_email: EmailStr | None = None
    billing_plan: str | None = Field(None, max_length=64)
    # Note: external_org_id and external_source are NOT mutable post-creation —
    # they identify the tenant's relationship to the upstream platform.
    # is_active is mutated via /deactivate and /reactivate, not PATCH.
```

### Adapter management schemas

```python
class AdapterConfigResponse(BaseModel):
    """Returned with secrets redacted (e.g., service_account_key_json: '<encrypted>')."""
    type: str
    configured: bool
    # Type-specific fields appear unencrypted only for non-secret values
    network_code: str | None = None  # GAM
    service_account_email: str | None = None  # GAM (email, not key)

class TestConnectionResponse(BaseModel):
    success: bool
    error: str | None = None
    tested_at: datetime
```

The `PUT /tenants/{id}/adapter-config` request body uses the same `AdapterConfig` discriminated union defined for provision.

### Error responses

Standard problem-detail shape:

```python
class ApiError(BaseModel):
    error: str           # machine-readable code, e.g. "external_org_id_conflict"
    message: str         # human-readable
    details: dict | None = None
```

| HTTP | error code | When |
|---|---|---|
| 400 | `adapter_connection_failed` | Adapter test connection failed |
| 404 | `tenant_not_found` | `{tenant_id}` doesn't exist |
| 409 | `external_org_id_conflict` | Provision: `external_org_id` already maps to a tenant; `details.tenant_id` points at the existing one |
| 409 | `tenant_has_active_resources` | Delete attempted while tenant has active media buys |
| 422 | (Pydantic validation) | Bad request shape |
| 500 | `internal_error` | Anything else |

## Endpoint behavior

### `POST /tenants/provision`

1. Validate request schema.
2. Check `external_org_id` doesn't already exist. 409 if it does.
3. Test the adapter connection *before* writing anything. 400 if it fails.
4. Open a transaction. In a single commit:
   - Create `Tenant` with `is_embedded=true`, `external_org_id`, `external_source`.
   - Create `AdapterConfig`.
   - Create default `CurrencyLimit` (USD or `default_currency`).
   - Create default `PropertyTag` (`all_inventory`).
   - If `initial_principal` provided, create `Principal` (no token — see sprint 2 § Auth boundary).
5. Set `session.info["management_api_caller"] = True`.
6. Return response.

The adapter test happens before the transaction so failures don't pollute the DB.

### `GET /tenants`

Returns `ListTenantsResponse`. Optional query params: `?is_embedded=true|false`, `?is_active=true|false`, `?external_source=scope3`.

### `GET /tenants/{id}`

Returns `TenantDetail`. 404 if not found.

### `PATCH /tenants/{id}`

1. Validate request schema (`UpdateTenantRequest`).
2. Look up tenant. 404 if not found.
3. Set `session.info["management_api_caller"] = True`.
4. Apply changes to the listed fields only. Empty/missing fields are not modified.
5. Commit and return updated `TenantDetail`.

The model guard ensures non-API callers can't make the same updates.

### `POST /tenants/{id}/deactivate` / `/reactivate`

Toggle `Tenant.is_active`. Idempotent — calling deactivate on an already-deactivated tenant is a no-op (200, returns current state). Returns updated `TenantDetail`.

### `DELETE /tenants/{id}`

Soft-delete by default (sets `is_active=false` and `deleted_at`). Hard-delete requires `?hard=true` and additional confirmation header (`X-Confirm-Delete: yes`). Returns 409 with `tenant_has_active_resources` if active media buys exist.

### `GET /tenants/{id}/adapter-config`

Returns `AdapterConfigResponse` with secrets redacted. Used to verify configuration without exposing credentials.

### `PUT /tenants/{id}/adapter-config`

Replaces the entire adapter config. Same shape as the `adapter` field in `ProvisionTenantRequest`. Tests connection before commit (same as provision); 400 on test failure, no change applied.

### `POST /tenants/{id}/adapter-config/test-connection`

Tests the *currently saved* adapter config without modifying it. Returns `TestConnectionResponse`. Used by Scope3 for periodic health checks.

## Middleware: identity propagation reader

Lives in `src/admin/middleware/identity_propagation.py` (new module).

```python
@dataclass
class PropagatedIdentity:
    email: str
    org_id: str
    role: Literal["admin", "member", "viewer"]
    source: str
    user_id: str | None
    signature: str | None  # only populated when IDENTITY_TRUST_MODE=signed

def read_identity_from_request(request) -> PropagatedIdentity | None:
    """Return identity headers, or None if absent. Caller decides if absent is allowed."""
    headers = request.headers
    if "X-Identity-Email" not in headers:
        return None
    return PropagatedIdentity(
        email=headers["X-Identity-Email"],
        org_id=headers["X-Identity-Org-Id"],
        role=headers["X-Identity-Role"],
        source=headers["X-Identity-Source"],
        user_id=headers.get("X-Identity-User-Id"),
        signature=headers.get("X-Identity-Signature"),
    )
```

Sprint 1 only ships the *reader* — the middleware that scopes requests by `external_org_id` is sprint 4 (when the UI middleware lands). For sprint 1, the Tenant Management API endpoints don't use this — they're API-key authenticated, not user-identity authenticated.

## spectree wiring

Add to `pyproject.toml`:

```toml
spectree = "^1.4"  # check current version
```

In `src/admin/tenant_management_api.py`:

```python
from spectree import SpecTree, Response

spec = SpecTree(
    "flask",
    title="Sales Agent — Tenant Management API",
    version="v1",
    path="docs",  # Swagger UI at /api/v1/tenant-management/docs
    openapi_url_prefix="",  # /openapi.json relative to blueprint root
)

@tenant_management_api.route("/tenants/provision", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(json=ProvisionTenantRequest, resp=Response(HTTP_201=ProvisionTenantResponse, HTTP_400=ApiError, HTTP_409=ApiError))
def provision_tenant(json: ProvisionTenantRequest):
    ...

# After all routes are registered:
spec.register(tenant_management_api)
```

Result:
- `GET /api/v1/tenant-management/openapi.json` → spec
- `GET /api/v1/tenant-management/docs` → Swagger UI
- Request/response validation enforced automatically.

## Reverse-proxy verification

Sprint 1 doesn't need to *land* the proxy — Scope3 sets that up — but it must verify the salesagent works under a path prefix. Two checks:

1. **Manual smoke test**: run the salesagent locally behind an nginx that mounts it at `/storefront/salesagent/`. Confirm: login redirects work, JS fetches use `scriptRoot`, no hardcoded URLs.
2. **Audit pass**: grep for hardcoded URLs in templates and JS. CLAUDE.md pattern #6 is enforced for new code, but pre-existing violations may exist.

```bash
# Templates
grep -rn 'href="/' templates/ | grep -v "{{ url_for"
grep -rn 'fetch("/' templates/

# JS files
grep -rn 'fetch("/\|fetch(\x27/' static/js/
```

Anything that turns up gets a `request.script_root` fix.

## Acceptance criteria

**Migrations:**
- [ ] Migration runs forward and backward cleanly on a populated dev DB.

**Provisioning:**
- [ ] `POST /tenants/provision` with valid GAM config creates a tenant and returns the expected response shape.
- [ ] Provision rolls back cleanly on adapter-test failure (no tenant row, no adapter config row).
- [ ] Provision returns 409 (not 500) on duplicate `external_org_id`.

**Tenant lifecycle CRUD:**
- [ ] `GET /tenants` lists managed tenants with filter params.
- [ ] `GET /tenants/{id}` returns 200 for existing, 404 for missing.
- [ ] `PATCH /tenants/{id}` updates platform-managed fields; rejects unknown fields (Pydantic `extra="forbid"` in dev/CI).
- [ ] `PATCH /tenants/{id}` does *not* allow modifying `external_org_id` or `external_source`.
- [ ] `POST /tenants/{id}/deactivate` is idempotent; second call returns current state.
- [ ] `POST /tenants/{id}/reactivate` works after deactivate.
- [ ] `DELETE /tenants/{id}` soft-deletes by default; hard-delete requires `X-Confirm-Delete: yes` header.
- [ ] `DELETE /tenants/{id}` returns 409 when active media buys exist.

**Adapter management:**
- [ ] `GET /tenants/{id}/adapter-config` returns config with secrets redacted (no service account key in response body).
- [ ] `PUT /tenants/{id}/adapter-config` tests connection before commit; rolls back on failure.
- [ ] `POST /tenants/{id}/adapter-config/test-connection` returns success/failure without modifying state.

**OpenAPI:**
- [ ] `GET /openapi.json` returns valid OpenAPI 3 spec listing all sprint 1 endpoints with full schemas.
- [ ] `GET /docs` renders Swagger UI; every endpoint executable from the UI with an API key.

**Write guard:**
- [ ] Attempting to update a managed tenant's platform-managed columns (name, billing_plan, external_org_id, etc.) via any non-API path raises `ManagedTenantWriteError`.
- [ ] Attempting to update a managed tenant's `AdapterConfig` via any non-API path raises `ManagedTenantWriteError`.
- [ ] Updating a publisher-managed table (Product, Principal, Creative) for a managed tenant via the **existing UI handlers** succeeds — the guard does not fire.
- [ ] The super-admin override flag (`session.info["super_admin_override"]`) bypasses the guard cleanly.

**Integration:**
- [ ] Reverse-proxy smoke test: salesagent admin UI works correctly when mounted at a non-root path prefix.
- [ ] End-to-end test: provision a managed tenant via API; update its name via API (succeeds); attempt to update its name via UI handler (fails with `ManagedTenantWriteError`); mutate a Product via UI handler (succeeds); deactivate via API; verify subsequent provision attempts with the same `external_org_id` fail or are blocked correctly.

**Schema testing:**
- [ ] Unit test: each Pydantic schema validates the happy path and rejects each failure mode (missing required fields, bad currency code, etc.).

## Open questions for sprint 1 specifically

1. **GAM credentials handling.** Storing the service account key JSON requires encryption at rest (existing `src/core/database/encryption.py` covers this). Confirm the `AdapterConfig` model already encrypts secret fields, or add it as part of sprint 1.
2. **Adapter test connection** — does the existing GAM adapter expose a `test_connection()` method, or do we need to add one? If the latter, that's a small extra item in sprint 1.
3. **Initial principal API token format.** Existing principal tokens come from `secrets.token_urlsafe()`. Confirm same scheme for managed-tenant principals; document the format in the response schema.

## What sprint 2 builds on this

Sprint 1 closes the **platform-managed** scope. Sprint 2 expands the API into the **publisher-managed** scope as automation/bulk-management conveniences (these are also editable from the UI, so they're not blockers):

- `POST/GET/PATCH/DELETE /tenants/{id}/principals[/{pid}]` + token rotation
- `POST/GET/PATCH/DELETE /tenants/{id}/products[/{pid}]`
- `POST /tenants/{id}/products/autogenerate-from-gam`

All reuse the same spectree + Pydantic + management-API-key plumbing established in sprint 1. The model-layer write guard does not need to fire on these — they're publisher-managed.
