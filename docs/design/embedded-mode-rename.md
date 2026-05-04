# Rename "managed mode" → "embedded mode"

**Status:** In progress — codebase rename underway, docs renamed
**Last updated:** 2026-05-04

## Why

PSA's "managed mode" was named when Scope3 was the first concrete
adopter. The abstraction is broader: it's how PSA is *embedded* into a
host product — any SSP console, publisher tools SaaS, wrapper-management
service, or other host where the host owns identity, branding, billing,
and chrome. "Managed mode" muddles that with "is_active" / lifecycle
semantics that already exist on the Tenant. Naming it "embedded mode"
also signals positioning for adoption: PSA can ship inside another
product, not just be hosted on its own.

Renaming to **embedded mode** clarifies positioning for adoption — it
signals "PSA can be embedded in your product" and makes the API surface
readable to teams who aren't Scope3.

## What's Scope3-specific today (must generalize)

The reverse-proxy work shipped in sprint 1 / 1.5 / 1.7 was driven by
Scope3's iframe integration but is broadly useful for any host:

| Surface | Today's Scope3-coupled bit | Generalize to |
|---|---|---|
| Banner copy | "Platform settings managed by Scope3 Storefront." | "Platform settings managed by {{ tenant.external_source \| title }}" |
| `external_source` defaults | hardcoded `"scope3"` in test fixtures + a few code paths | Make required at provision; no default |
| Iframe chrome stripping | `?embedded=1` query param (already platform-agnostic) | No change needed |
| `psa:navigate` postMessage origin | Reads `tenant.public_agent_url` host (already platform-agnostic) | No change needed |
| `X-Identity-*` propagation | Already platform-agnostic by design | No change needed |
| `CustomProxyFix` overlap-stripping | Already platform-agnostic | No change needed |
| Healthcheck path `/health` | Already platform-agnostic | No change needed |

**Core finding:** the reverse-proxy infra is already platform-agnostic.
Scope3-coupling is mostly in copy and test fixtures, not the protocol.

## Rename surface (sweeping, mechanical)

### Database column
- `Tenant.managed_externally` (bool) → **`Tenant.is_embedded`** (bool)
- Migration renames the column; default false; no backfill needed.
- All existing rows with `managed_externally=true` map cleanly to
  `is_embedded=true`.

### Pydantic schemas
- `ProvisionTenantResponse.managed_externally: Literal[True]` →
  `is_embedded: Literal[True]`
- `TenantSummary.managed_externally: bool` → `is_embedded: bool`
- `TenantDetail.managed_externally: bool` → `is_embedded: bool`
- All API responses change wire shape — coordinate with Storefront
  (and any other adopters) before flipping.

### Code identifiers
- `managed_tenant_guard.py` → `embedded_tenant_guard.py`
- `ManagedTenantWriteError` → `EmbeddedTenantWriteError`
- `managed_tenant_api.py` design doc references → `embedded_tenant_api.py`
  - **Endpoint URL stays `/api/v1/tenant-management/`** — that's about
    *managing tenants* (CRUD on the Tenant resource), not about the
    embedded-mode flag. No URL change.
- `managed_mode_auth.py` → `embedded_mode_auth.py`
- All `# managed-mode` / `# managed-externally` comments → embedded-mode

### Documentation
- The original design docs were named `managed-tenant-mode-*.md`; renamed
  to `embedded-mode-*.md` as part of the sweep. All cross-references
  updated.
- Search-and-replace "managed mode" → "embedded mode" /
  "managed-mode" → "embedded-mode" (case-preserving) in all docs.
- "managed_externally" → "is_embedded" in every code reference.

### What does NOT rename
- **`tenant.is_active`** — different concept (lifecycle on/off).
- **`Tenant Management API`** — that's the API for managing tenants
  (CRUD on Tenant), regardless of whether they're embedded or not.
  Open-instance tenants also use it (provision flow).
- **`X-Tenant-Management-API-Key`** — same reason.
- **`X-Identity-*` headers** — already platform-agnostic, no change.

## Test fixture audit

Test fixtures with hardcoded `external_source="scope3"` should keep that
value (it's a realistic test case) but the *code* must not have a
default fall-through to "scope3" anywhere — the field is required at
provision and that's the contract.

Sweep targets (illustrative; full list at refactor time):
- `tests/integration/test_managed_tenant_api.py::_provision_payload` —
  fine to keep `"scope3"`, it's exercising the field.
- Banner template strings that say "Scope3" rather than reading the
  tenant's `external_source` — these break for non-Scope3 adopters and
  must be parameterized.

## Migration plan

Single-PR sweep is preferable — splitting "DB column rename" from
"code rename" leaves the codebase in a half-renamed state for a
deploy cycle, which is brittle.

1. Alembic migration: rename `tenants.managed_externally` →
   `tenants.is_embedded` (Postgres `ALTER TABLE ... RENAME COLUMN`,
   atomic, fast).
2. Code-wide rename via grep + manual review (rules above).
3. Documentation rename + cross-reference update.
4. Run full test suite; expected to be a mechanical no-op.
5. Coordinate the wire-shape change with Storefront ahead of merge.

## Sequencing

**After Sprint 1.8** lands end-to-end. Doing the rename mid-sprint
creates merge conflicts on every in-flight piece (B, C, E already use
`managed_externally`); doing it cleanly after 1.8 is one focused PR.

Estimated scope: **~0.5–1 day** including the wire-shape coordination
with Storefront.
