# Sprint 4 Spec: Publisher-Managed CRUD via API (Optional)

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1](./embedded-mode-sprint-1.md), [sprint 1.5](./embedded-mode-sprint-1.5.md), [sprint 2](./embedded-mode-sprint-2.md), [sprint 3](./embedded-mode-sprint-3.md)
**Status:** Draft, optional
**Last updated:** 2026-05-04

> **Reference deployment.** Concrete examples cite Scope3 Storefront as the first reference deployment. The deliverables are generic.

## Scope

Sprint 4 expands the Tenant Management API into the **publisher-managed scope**: principals (advertisers) and products. These surfaces are also editable from the proxied UI, so the API is purely an automation/bulk-management convenience — **not a prerequisite for an embedded-mode launch**. Build this only when there's a concrete need to manage these entities programmatically (bulk advertiser onboarding, scripted product autogeneration, etc.).

Reasons to do sprint 4:

1. A host product may want to push initial product catalogs in bulk (especially via `autogenerate-from-gam`) rather than have publishers configure them in the UI.
2. Bulk advertiser onboarding from a host-side CSV/import is much smoother via API.
3. The plumbing established in sprint 1 (spectree, Pydantic, management-API-key, ORM repositories) makes adding these endpoints cheap.

11 endpoints in total:

```
# Principals (no token management — see "Auth boundary" section below)
GET     /tenants/{tid}/principals
POST    /tenants/{tid}/principals
GET     /tenants/{tid}/principals/{pid}
PATCH   /tenants/{tid}/principals/{pid}
DELETE  /tenants/{tid}/principals/{pid}

# Products
GET     /tenants/{tid}/products
POST    /tenants/{tid}/products
GET     /tenants/{tid}/products/{pid}
PATCH   /tenants/{tid}/products/{pid}
DELETE  /tenants/{tid}/products/{pid}
POST    /tenants/{tid}/products/autogenerate-from-gam
```

## Auth boundary

Token management is intentionally not part of the Tenant Management API. On an embedded instance, the salesagent doesn't authenticate callers at all — network ACL is the trust boundary. Any service that can reach the salesagent's port is, by definition, authorized.

This applies uniformly:
- **UI proxy**: identity comes from `X-Identity-*` headers, trusted because the network is private.
- **Tenant Management API**: API key (the one credential that crosses the trust boundary on purpose, identifying the control plane).
- **MCP/A2A buyer protocol**: no protocol-level auth on embedded instances. Caller specifies which principal/tenant the call is on behalf of via headers (likely `X-Principal-Id`, plus `X-Tenant-Id` if not derivable). Same network-trust model as the UI proxy.

This means:
- `POST /principals` does not return an API token. Principals are pure advertiser-identity records.
- There is no token rotation, regeneration, or revocation endpoint.
- `PrincipalSummary` and `PrincipalDetail` carry no token-related fields.
- The existing `Principal.token` column stays in the schema for open-instance compatibility but is unused for embedded-tenant principals.

**For open instances** (today's behavior): MCP/A2A still uses `x-adcp-auth` bearer tokens per principal. This is unchanged. The principal's token is still generated, stored, and used. Embedded mode is the variant that opts out of per-principal credentials in favor of network trust.

**Implementation note**: the salesagent's MCP/A2A endpoints need to know how to scope calls without a bearer token in embedded mode. That `resolve_identity()` change is shipped in [sprint 2](./embedded-mode-sprint-2.md) (embedded-mode hardening). Sprint 4 just consumes the existing infrastructure.

Out of scope for sprint 4 (in other sprints):
- Creative review/approval API (publishers do this in the UI; not commonly automated)
- Workflow approval API (already in [sprint 3](./embedded-mode-sprint-3.md))
- Authorized properties / inventory profiles / business rules / agents / policy ([sprint 5](./embedded-mode-sprint-5.md), also optional)
- Bulk endpoints for principals (`POST /principals/bulk`). Single-create + reasonable rate limits is fine for v1; bulk added if a host needs it.

The model-layer write guard from sprint 1 is **not** modified. Principals, Products, and their child tables are publisher-managed — the existing UI handlers continue to write to them without the guard firing.

## Pydantic schemas

Lives in `src/admin/api_schemas/principals.py` and `src/admin/api_schemas/products.py`. Follow CLAUDE.md pattern #1: extend `adcp` library schemas via inheritance with the `Library*` alias convention.

### Principal schemas

```python
from adcp.types import Principal as LibraryPrincipal
from pydantic import BaseModel, Field

class PrincipalCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    external_advertiser_id: str | None = Field(None, max_length=255)
    # Adapter-specific advertiser ID mappings (e.g., GAM advertiser ID).
    # Validated by the adapter on create — invalid IDs return 400.
    adapter_mappings: dict[str, str] | None = None
    # Optional: testing-mode config (used by mock adapter, ignored by GAM)
    testing_config: dict | None = None

class PrincipalUpdateRequest(BaseModel):
    """PATCH — all fields optional. Sparse update."""
    name: str | None = Field(None, min_length=1, max_length=255)
    external_advertiser_id: str | None = Field(None, max_length=255)
    adapter_mappings: dict[str, str] | None = None
    testing_config: dict | None = None

class PrincipalSummary(BaseModel):
    principal_id: str
    name: str
    tenant_id: str
    external_advertiser_id: str | None
    created_at: datetime
    updated_at: datetime

class PrincipalDetail(PrincipalSummary):
    adapter_mappings: dict[str, str]

class ListPrincipalsResponse(BaseModel):
    principals: list[PrincipalSummary]
    count: int
```

### Product schemas

Products are heavily AdCP-spec-driven. The library's `Product` type carries most fields. Sprint 4 schemas extend it minimally:

```python
from adcp.types import Product as LibraryProduct
from pydantic import BaseModel, Field

class ProductCreateRequest(LibraryProduct):
    """Library Product fields are required as-is. Internal-only fields excluded."""
    # implementation_config is internal-only and NOT accepted from the API.
    # If a deployment needs to set per-product implementation config, that's a
    # future API change with explicit scoping — not freely settable.
    pass

class ProductUpdateRequest(BaseModel):
    """PATCH — sparse update. Only library Product fields are settable."""
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    # ... full set of optional fields mirroring LibraryProduct, all defaulting None.
    # Rather than enumerate here, generate this schema programmatically from
    # LibraryProduct with all fields made Optional. See implementation note below.

class ProductSummary(BaseModel):
    """Lighter view for list endpoints."""
    product_id: str
    name: str
    tenant_id: str
    pricing_model: str
    base_price: Decimal | None
    currency: str
    formats_supported: list[str]
    is_active: bool
    created_at: datetime

class ProductDetail(LibraryProduct):
    """Full library Product fields, plus internal metadata."""
    product_id: str
    tenant_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

class ListProductsResponse(BaseModel):
    products: list[ProductSummary]
    count: int

# Autogenerate

class AutogenerateProductsRequest(BaseModel):
    """Generate products by querying GAM inventory."""
    # Filter what to autogenerate from
    parent_ad_unit_id: str | None = None  # if set, only descendants of this ad unit
    include_inactive_ad_units: bool = False
    # Defaults applied to every generated product
    default_pricing_model: Literal["CPM", "VCPM", "CPC", "FLAT_RATE"] = "CPM"
    default_base_price: Decimal = Decimal("5.00")  # USD per 1000 impressions
    default_currency: str = Field("USD", min_length=3, max_length=3)
    # Behavior
    dry_run: bool = False        # if true, return what *would* be created without writing
    skip_existing: bool = True   # skip ad units that already have a product mapped

class AutogeneratedProductInfo(BaseModel):
    product_id: str | None       # null in dry_run
    name: str
    source_ad_unit_id: str
    source_ad_unit_path: str
    status: Literal["created", "skipped_existing", "skipped_inactive", "failed"]
    error: str | None = None     # populated when status="failed"

class AutogenerateProductsResponse(BaseModel):
    dry_run: bool
    total_ad_units_considered: int
    created: list[AutogeneratedProductInfo]
    skipped: list[AutogeneratedProductInfo]
    failed: list[AutogeneratedProductInfo]
```

**Programmatic ProductUpdateRequest**: enumerating ~25 optional fields is duplication. Use Pydantic's `model_construct` or generate a partial via:

```python
from pydantic import create_model
from adcp.types import Product as LibraryProduct

ProductUpdateRequest = create_model(
    "ProductUpdateRequest",
    **{name: (field.annotation | None, None) for name, field in LibraryProduct.model_fields.items()
       if name != "implementation_config"},
)
```

Confirm at implementation time whether `LibraryProduct` fields support this idiom; if not, hand-write the schema once and lock it with the schema-inheritance structural guard.

## Endpoint behavior

### Principals

#### `GET /tenants/{tid}/principals`

Returns `ListPrincipalsResponse`. Optional query params:
- `?has_active_token=true|false` — filter by token state
- `?external_advertiser_id={id}` — exact match lookup

Returns `[]` not 404 when tenant exists but has no principals. 404 only if tenant doesn't exist.

#### `POST /tenants/{tid}/principals`

1. Validate request schema.
2. Validate `tenant_id` exists. 404 if not.
3. Validate `adapter_mappings` against the tenant's adapter (e.g., GAM verifies the advertiser ID exists in the network). 400 with adapter error if invalid.
4. Generate principal_id (UUID-ish per existing convention).
5. Insert `Principal` row.
6. Return `PrincipalDetail`.

No token is generated or returned. Buyer-protocol auth in embedded mode flows through the identity-propagation contract; per-principal tokens are not part of the embedded-mode API surface.

**Conflict handling**: If a principal with the same `(tenant_id, name)` exists, return 409 `principal_name_conflict`.

#### `GET /tenants/{tid}/principals/{pid}`

Returns `PrincipalDetail`. 404 if missing or wrong tenant.

#### `PATCH /tenants/{tid}/principals/{pid}`

Sparse update. `external_advertiser_id` and `adapter_mappings` revalidated against the adapter when changed (so a mistyped GAM ID returns 400, not silently breaks).

#### `DELETE /tenants/{tid}/principals/{pid}`

Soft-delete by default (sets `deleted_at`). Returns 409 `principal_has_active_media_buys` if active buys exist; the principal must be settled or the buys cancelled before deletion.

Hard-delete (`?hard=true` + `X-Confirm-Delete: yes`) requires no active media buys, and removes all child records. Reserved for emergencies.

### Products

#### `GET /tenants/{tid}/products`

Returns `ListProductsResponse`. Optional query params:
- `?is_active=true|false`
- `?pricing_model=CPM|CPC|...`
- `?format={format_id}` — products that support a given format

Pagination: `?limit=N&offset=M`, default `limit=100`. Max 500. Total count in response.

#### `POST /tenants/{tid}/products`

1. Validate request schema (Pydantic enforces all required AdCP Product fields).
2. Validate adapter compatibility — e.g., GAM doesn't support CPCV, so a CPCV product on a GAM tenant returns 400 `pricing_model_unsupported`.
3. Insert `Product` row with `is_active=true` by default.
4. Return `ProductDetail`.

**Conflict handling**: If a product with the same `(tenant_id, name)` exists, return 409 `product_name_conflict`. Suggested mitigation: clients append a discriminator if creating dynamically.

#### `GET /tenants/{tid}/products/{pid}`

Returns `ProductDetail`. 404 if missing or wrong tenant.

#### `PATCH /tenants/{tid}/products/{pid}`

Sparse update. Pricing-model changes revalidated against adapter compatibility. `is_active` toggle is allowed (lets the host hide products without deleting).

#### `DELETE /tenants/{tid}/products/{pid}`

Soft-delete. Active media buys against this product remain functional but the product is hidden from new buys. Hard-delete (`?hard=true`) requires no active media buys.

#### `POST /tenants/{tid}/products/autogenerate-from-gam`

The marquee endpoint of sprint 4. Bootstraps a product catalog from the publisher's GAM inventory.

1. Validate request schema.
2. Validate tenant has a GAM adapter configured. 400 `adapter_not_gam` if not.
3. Query GAM for ad units (filtered by `parent_ad_unit_id` if provided).
4. For each ad unit:
   - If `skip_existing=true` and a product is already mapped to this ad unit, mark `skipped_existing`.
   - If ad unit is inactive and `include_inactive_ad_units=false`, mark `skipped_inactive`.
   - Else: build a `Product` from the ad unit (name = ad unit display name, formats from supported sizes, pricing from defaults).
5. If `dry_run=true`, return the would-be results without committing.
6. Else commit the batch in one transaction. Per-product failures are collected (validation errors, adapter rejections) and returned in `failed[]`. Successfully created products are committed; partial failure does not roll back successes — the caller sees exactly what was created and what wasn't.
7. Return `AutogenerateProductsResponse`.

**Synchronous, with timeout**: GAM ad-unit queries are typically fast for small/medium networks. Hard timeout at 60s; if GAM is slow, return 504 `gam_query_timeout` and the caller retries. Async-with-job-tracking is a future enhancement if a deployment hits this regularly.

**Idempotency**: re-running with the same parameters and `skip_existing=true` is safe — already-created products are skipped, not duplicated.

## Error responses

Reuses sprint 1's `ApiError` shape. New error codes:

| HTTP | error code | When |
|---|---|---|
| 400 | `adapter_validation_failed` | `adapter_mappings` invalid for the tenant's adapter (e.g., GAM advertiser ID not found) |
| 400 | `pricing_model_unsupported` | Product pricing model not supported by tenant's adapter |
| 400 | `adapter_not_gam` | `autogenerate-from-gam` called on non-GAM tenant |
| 409 | `principal_name_conflict` | `(tenant_id, name)` principal exists |
| 409 | `principal_has_active_media_buys` | Delete blocked |
| 409 | `product_name_conflict` | `(tenant_id, name)` product exists |
| 409 | `product_has_active_media_buys` | Delete blocked |
| 504 | `gam_query_timeout` | Autogenerate exceeded 60s |

## Repository extraction

Sprint 1 established a partial `_impl()` extraction pattern. Sprint 4 adds two new repositories that the API endpoints consume — and that the existing UI handlers can opportunistically migrate to:

```
src/core/repositories/principal_repository.py
  - list_for_tenant(tenant_id, filters) -> list[Principal]
  - get(tenant_id, principal_id) -> Principal | None
  - create(tenant_id, request) -> tuple[Principal, str]  # returns cleartext token
  - update(tenant_id, principal_id, request) -> Principal
  - soft_delete(tenant_id, principal_id) -> None
  - rotate_token(tenant_id, principal_id, grace_seconds) -> TokenRotation

src/core/repositories/product_repository.py
  - list_for_tenant(tenant_id, filters, pagination) -> tuple[list[Product], int]
  - get(tenant_id, product_id) -> Product | None
  - create(tenant_id, request) -> Product
  - update(tenant_id, product_id, request) -> Product
  - soft_delete(tenant_id, product_id) -> None
  - autogenerate_from_gam(tenant_id, request) -> AutogenerateResult
```

The existing UI blueprints (`src/admin/blueprints/principals.py`, `src/admin/blueprints/products.py`) still use inline DB code. Sprint 4 does **not** require migrating them — that's opportunistic future work. The structural-guard FIXME allowlist tracks the pre-existing violations.

## Acceptance criteria

**Schemas:**
- [ ] All Pydantic schemas validate happy-path and reject each documented failure mode.
- [ ] `ProductCreateRequest` extends `LibraryProduct` correctly per CLAUDE.md pattern #1; `implementation_config` is excluded.
- [ ] `ProductUpdateRequest` covers all `LibraryProduct` fields as optional (sparse PATCH).

**Principals:**
- [ ] `POST /principals` creates a principal and returns `PrincipalDetail` (no token in response).
- [ ] No endpoint in the API surface returns or accepts a principal API token.
- [ ] `DELETE /principals/{pid}` returns 409 when active media buys exist.
- [ ] Adapter validation: creating a principal with a non-existent GAM advertiser ID returns 400, not 500.

**Products:**
- [ ] `POST /products` rejects pricing models unsupported by the tenant's adapter.
- [ ] `PATCH /products/{pid}` partial update preserves un-mentioned fields.
- [ ] `DELETE /products/{pid}` returns 409 when active media buys exist.

**Autogenerate:**
- [ ] `dry_run=true` writes nothing; response shape matches non-dry-run.
- [ ] `skip_existing=true` returns `skipped_existing` for ad units already mapped.
- [ ] Re-running with the same params is idempotent (no duplicates).
- [ ] Per-product failures don't roll back successful creations.
- [ ] 504 returned cleanly on GAM timeout (not 500 / not partial state).
- [ ] Returns 400 `adapter_not_gam` for non-GAM tenants.

**Integration with prior sprints:**
- [ ] After sprints 1–4: provision an embedded tenant, autogenerate products from GAM, create a principal — entire flow works via API only without touching the UI.
- [ ] After sprints 1–4: existing UI handlers for principals/products on an embedded tenant still work — the model write guard does not fire on these tables.

**OpenAPI:**
- [ ] All 11 endpoints listed in the OpenAPI spec.
- [ ] Swagger UI executable with an API key for every endpoint.

## Open questions

1. **`resolve_identity()` change for MCP/A2A in embedded mode.** Concrete header names (`X-Principal-Id`, `X-Tenant-Id` vs. reusing existing identity headers), and how the salesagent toggles between bearer-token mode (open instance) and header-scope mode (embedded instance). Confirm the existing `resolve_identity()` callsites can branch cleanly on `MANAGED_INSTANCE`. Implementation detail, not a separate design doc.
3. **GAM ad-unit-to-product mapping table.** `skip_existing` requires knowing which products were autogenerated from which ad units. Add `Product.source_ad_unit_id` (nullable string) populated by autogenerate; query by it for the skip check. Does this exist today?
4. **Product field mapping from GAM ad units.** The autogenerate endpoint needs deterministic rules for mapping ad-unit attributes (sizes → formats, targeting → product targeting, etc.). Specify these mappings in a separate `gam-product-autogenerate-mapping.md` doc since they're substantive.
5. **Bulk principal creation.** Sprint 4 ships single-create. If a host needs to onboard hundreds at a time, add `POST /principals/bulk` accepting a list. Defer until concretely requested.

## What sprint 5+ builds on this

If sprint 4 ships, [sprint 5](./embedded-mode-sprint-5.md) (also optional) fills out the remaining publisher-managed sub-resources via API (properties, profiles, slack, business rules, agents, policy) — same plumbing, different tables. [Sprint 6](./embedded-mode-sprint-6.md) (optional) adds outbound webhooks.
