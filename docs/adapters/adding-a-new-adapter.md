# Adding a New Adapter — Checklist & Playbook

This doc captures every step needed to ship a new ad-server adapter end-to-end:
discovery, scaffolding, registration, UI, API surface, tests, and docs.

**Reference implementation: the FreeWheel adapter** (`src/adapters/freewheel/`,
PR [#381](https://github.com/bokelley/salesagent/pull/381)). Every section below
points to the equivalent FW file so you can copy structure verbatim.

---

## Phase 0 — Discovery (before writing any code)

Save days of rework by probing the API first. Most ad servers have *more*
restrictive IAM than their public docs suggest.

- [ ] **Read the public API reference end-to-end.** Note: REST vs SOAP, JSON
      vs XML, sync vs async report jobs, auth flow (OAuth2 password grant,
      `client_credentials`, JWT, static bearer, etc).
- [ ] **Probe scope with an authenticated test token.** Run a short script
      that hits every endpoint you'll need (list/read/write per entity).
      Three distinct failure modes are diagnostic:
  - `200` → real surface, scope granted.
  - `403 {"Message": "User is not authorized... explicit deny in an identity-based policy"}` → real surface, **scope grant needed**.
  - `404` → endpoint doesn't exist at that URL, try variants.
  - HTML 403 page → wrong host or wrong API version.
  See `/tmp/fw-probe-reporting.py` in the FW PR for the pattern.
- [ ] **Map the platform's data model to AdCP entities.** For each of these,
      decide what the platform's equivalent is:
  - `MediaBuy` → the commercial transaction (FW IO; GAM Order)
  - `Package` → the delivery unit (FW Placement; GAM LineItem)
  - `Creative` → asset record + placement binding (FW `creative_resources` + `creative_instances`; GAM Creative + LICA)
  - `Reporting` → delivery counters (separate Query Reporting API for FW; ReportService for GAM)
- [ ] **Document scope blockers up front.** Each `403 IAM-deny` is a scope-grant
      ask. File them in the adapter's README under "Scope grants still needed"
      (FW reference: `docs/adapters/freewheel/README.md`). Tier them by
      what they unblock.
- [ ] **Decide the auth model.** Recommend a persistent technical
      username+password over rotating bearer tokens — the integration handles
      auto-refresh and you avoid weekly manual rotation.

---

## Phase 1 — Adapter package scaffolding

Files to create under `src/adapters/<name>/`:

- [ ] `__init__.py` — export `<Name>Adapter`, `<Name>ConnectionConfig`,
      `<Name>ProductConfig` (and any exception classes the rest of the codebase
      catches).
- [ ] `_transport.py` *(optional but recommended)* — HTTP transport with
      auth handling (token mint, cache, refresh on 401), retry/backoff,
      content-type handling. Reference: `src/adapters/freewheel/_transport.py`.
- [ ] `client.py` — public API client that composes per-resource sub-clients
      (e.g. `client.inventory.*`, `client.commercial.*`). Reference:
      `src/adapters/freewheel/client.py`.
- [ ] `entities.py` *(optional)* — Pydantic models for upstream API entities.
      Useful when responses are complex enough that raw dicts get unwieldy.
- [ ] `schemas.py` — **required.** Two Pydantic models:
  - `<Name>ConnectionConfig(BaseConnectionConfig)` — tenant-level credentials
    + environment. Secrets must serialize through Fernet (see FW's
    `_encrypt_password` / `_decrypt_password` `@field_serializer` pattern).
    Reference: `src/adapters/freewheel/schemas.py`.
  - `<Name>ProductConfig(BaseProductConfig)` — per-product targeting and
    delivery selectors. Inventory IDs, audience IDs, pricing, etc.
- [ ] `adapter.py` — the `AdServerAdapter` subclass. **Required overrides**
      (see `src/adapters/base.py`):
  - `adapter_name` (class attribute, lowercase)
  - `default_channels` (e.g. `["olv", "ctv", "display"]`)
  - `default_delivery_measurement` (e.g. `{"provider": "freewheel"}`)
  - `connection_config_class` + `product_config_class`
  - `capabilities = AdapterCapabilities(...)` — declares what the adapter
    can do (used by the UI and the discovery API)
  - `__init__` — resolves principal + advertiser + auth
  - `create_media_buy` (abstract)
  - `add_creative_assets` (abstract)
  - `associate_creatives` (abstract)
  - `check_media_buy_status` (abstract)
  - `get_media_buy_delivery` (abstract; can return empty until reporting
    scope is granted)
  - `update_media_buy` (abstract)
  - `process_assets` (abstract; inherited from `CreativeEngineAdapter`)
  - `get_supported_pricing_models` (override; what the platform actually supports)
  - `get_targeting_capabilities` (override; declares geo/postal/DMA support)
  - `get_creative_formats` (override if you ship platform-specific formats)
  - `get_available_inventory` (async; override to surface synced inventory
    to the AI product configurator)
- [ ] `targeting.py` *(optional but recommended)* — translates AdCP
      `Targeting` overlays into the platform's targeting JSON/XML, with a
      `validate_targeting()` helper that rejects unsupported dimensions
      (e.g. postal targeting on FW).
- [ ] `formats.py` *(optional)* — static `Format` declarations if the platform
      has a small known set (video pre/mid/post-roll, banner sizes, etc.).
      Reference: `src/adapters/freewheel/formats.py`.

---

## Phase 2 — Inventory cache (if the platform has stable inventory)

Skip if the adapter doesn't need a synced inventory taxonomy. Most do.

- [ ] **Alembic migration** for `<name>_inventory` table:
      `(tenant_id, entity_type, entity_id)` primary key, `name`, `parent_id`,
      `raw_json`, `last_synced_at`. Reference:
      `alembic/versions/7c3073bd70cf_add_freewheel_inventory_cache.py`.
- [ ] **ORM model** in `src/core/database/models.py`. Use `JSONType` for the
      JSON blob, `String` for IDs. Cascade-delete on tenant. Reference: the
      `FreeWheelInventory` class.
- [ ] **Repository** in `src/core/database/repositories/<name>_inventory.py`
      — tenant-scoped reads + Postgres `ON CONFLICT DO UPDATE` bulk upsert.
      All ORM model queries **must** go through a repository (enforced by
      `test_architecture_no_raw_select`). Reference:
      `src/core/database/repositories/freewheel_inventory.py`.
- [ ] **Sync service** in `src/adapters/<name>/inventory_sync.py` — walks
      every entity type, returns a `SyncResult(per-entity counts + errors)`,
      writes through the repository. Reference:
      `src/adapters/freewheel/inventory_sync.py`.

---

## Phase 3 — Reporting cache (if the platform has separate reporting API)

Many platforms (FW, GAM, etc.) put delivery metrics on a separate API
surface. If yours does, build the **read path first** anchored on AdCP's
contract; you can ship the actual reporting client later.

- [ ] **Migration** for `<name>_placement_stats` (or similar): per-package
      `impressions`, `spend_micros`, `completed_views`, `clicks`, `currency`,
      `delivery_status`, `as_of`, `last_synced_at`. **Spend in micros**
      (1 EUR = 1_000_000) to avoid floating-point drift. Reference:
      `alembic/versions/190d6e98754b_add_freewheel_placement_stats.py`.
- [ ] **ORM model** + **repository** with `get_by_placement_ids(ids)` and
      `list_by_insertion_order(io_id)` and `bulk_upsert(rows)` methods.
- [ ] **`get_packages_snapshot()` override** that reads from the cache and
      returns `Snapshot` per package — `None` for missing rows (don't fabricate).
      Map the platform's status to the AdCP `DeliveryStatus` enum.
- [ ] **`get_media_buy_delivery()` override** that aggregates cache rows into
      `DeliveryTotals` + `AdapterPackageDelivery` list. Empty cache → fall
      through to `_empty_delivery_response()` (don't error).
- [ ] **Sync stub** at `src/adapters/<name>/reporting_sync.py` raising a
      clearly-named exception (e.g. `ReportingScopeNotGranted`) with a pointer
      to the README scope ask. Day-of-scope: implement four private methods
      (`_submit_job` / `_poll_job` / `_fetch_results` / `_parse_rows`).
      Reference: `src/adapters/freewheel/reporting_sync.py`.

---

## Phase 4 — Register the adapter

Three places. Miss any one and the adapter is unreachable.

- [ ] **`src/adapters/__init__.py`** — add to `ADAPTER_REGISTRY`. Use lowercase
      canonical key (e.g. `"freewheel"`); aliases are fine but the canonical
      key is what flows through the rest of the system.

- [ ] **Typed config in `src/admin/api_schemas/tenant_management.py`** —
      embedder-facing discriminated union. Add `<Name>AdapterConfig(BaseModel)`
      with `type: Literal["<name>"]` plus the connection fields (use
      `SecretStr` for secrets). Add it to the `AdapterConfig` discriminated
      union. **Without this, typed clients (Scope3 storefront) cannot
      provision tenants on your adapter.**

- [ ] **Persistence + dict mapping in `src/admin/tenant_management_api.py`** —
      update `_adapter_config_to_dict()` and `_persist_adapter_config()`
      to handle the new type. Round-trip secrets through the adapter's own
      `<Name>ConnectionConfig` so Fernet encryption lands consistently in
      `AdapterConfig.config_json`.

- [ ] **Discovery catalog in `src/admin/tenant_management_api.py`** — add
      entries to:
  - `_ADAPTER_CATALOG_METADATA` — display name + description
  - `_ADAPTER_CONFIG_TYPED` — pointer to the typed schema
  Without these the adapter won't appear in `GET /api/v1/tenant-management/adapters`.

---

## Phase 5 — Admin UI

- [ ] **Picker card** in `templates/tenant_settings.html` (search for
      `selectAdapter('freewheel')` and copy the surrounding `<div>` block).
      One card per adapter.
- [ ] **`templates/adapters/<name>/connection_config.html`** — the form
      shown when the operator picks this adapter. Fields should match
      `<Name>ConnectionConfig`. Include a `Test Connection` button and a
      status placeholder div for the JS to populate. Reference:
      `templates/adapters/freewheel/connection_config.html`.
- [ ] **`templates/adapters/<name>/product_config.html`** *(optional)* —
      the per-product targeting picker. Pickers should declare
      `data-entity-type` and use the inventory query endpoint to populate.
      Reference: `templates/adapters/freewheel/product_config.html`.
- [ ] **All JS must use `request.script_root`** (Critical Pattern #6) so the
      UI works behind reverse proxies:

      ```js
      const scriptRoot = '{{ request.script_root }}' || '';
      const apiUrl = scriptRoot + '/api/tenant/' + tenantId + '/adapters/<name>/inventory';
      ```

---

## Phase 6 — Admin API endpoints

In `src/admin/blueprints/adapters.py`. Pre-existing FreeWheel + Broadstreet
routes are the precedent.

- [ ] **`POST /api/tenant/<tenant_id>/adapters/<name>/test-connection`** —
      validates submitted credentials by minting a token / hitting a probe
      endpoint. Decorate with
      `@require_tenant_access(role=("admin",), allow_embedded_writes=True)`
      and reject submitted ciphertext on secret fields (cross-tenant replay defence).
- [ ] **`GET /api/tenant/<tenant_id>/adapters/<name>/inventory`** *(if cache)* —
      reads from the inventory repository, returns flat list keyed by
      `entity_type` query param. Used by the product config UI pickers.
- [ ] **`POST /api/tenant/<tenant_id>/adapters/<name>/sync-inventory`**
      *(if cache)* — triggers a fresh sync. Use `role=("admin",)`. Returns the
      sync result.

---

## Phase 7 — Tests

Mandatory minimums:

- [ ] **`tests/unit/test_<name>_schemas.py`** — round-trip every config field
      through `model_dump → model_validate`. Cover encryption (secrets
      serialize to ciphertext; values round-trip; pre-encrypted values not
      double-encrypted).
- [ ] **`tests/unit/test_<name>_adapter.py`** — registry wiring, dry-run
      behaviour, `__init__` cred validation, `get_supported_pricing_models`,
      `get_targeting_capabilities`. If you override
      `get_available_inventory`, test the shape via a mocked repository.
- [ ] **`tests/unit/test_<name>_transport.py`** *(if you have a transport)* —
      mock at the `requests.Session` level, verify auth header, retry,
      token-refresh-on-401 behaviour.
- [ ] **`tests/unit/test_<name>_targeting.py`** *(if you translate targeting)* —
      every AdCP field's mapping, every rejection path.
- [ ] **`tests/unit/test_<name>_inventory_sync.py`** *(if you sync inventory)* —
      mock the client, verify per-entity counts, error capture, idempotent
      upsert.
- [ ] **`tests/unit/test_<name>_reporting_cache.py`** *(if you have a reporting
      cache)* — read paths return `None` / empty response on empty cache,
      `Snapshot` / `DeliveryTotals` shapes when populated, status enum mapping.
- [ ] **`tests/integration/test_<name>_live.py`** — marked `@pytest.mark.live`,
      skipped by default in CI. Runs against the real API when
      `<NAME>_TEST_API_KEY` is set. Verifies token validity, list-side reads,
      and a full create-and-delete cycle. Reference:
      `tests/integration/test_freewheel_live.py`.
- [ ] **Add typed-config tests in `tests/unit/test_tenant_management_schemas.py`** —
      happy path, rejection paths, and a discriminator-routing test through
      `ProvisionTenantRequest.model_validate(...)`.
- [ ] **Add the catalog assertion in `tests/integration/test_tenant_management_api_integration.py::test_list_adapters_returns_supported_catalog`** —
      include the new type in the expected set.

---

## Phase 8 — Documentation

- [ ] **`docs/adapters/<name>/README.md`** — entity mapping table, auth
      paths, capabilities matrix, live coverage matrix (✅/🟡/⏳/❌ per
      method), scope grants still needed (tiered by what they unblock),
      constraints. Reference: `docs/adapters/freewheel/README.md`.
- [ ] **Update `docs/adapters/README.md`** — add a section to the
      "Available Adapters" list and a row to the "Choosing an Adapter" table.

---

## Phase 9 — Regenerate machine-readable specs

- [ ] `make openapi` — regenerates `docs/api/tenant-management-openapi.{json,yaml}`.
      The `test_committed_openapi_json_matches_live_spec` unit test will
      fail otherwise.
- [ ] Do not add adapter-specific OpenAPI artifacts. New embedded setup
      capabilities belong in the generic tenant-management/composition APIs:
      adapter capabilities, config-schema, wholesale products, selector
      discovery, signal mapping, and preview/validation.

---

## Phase 10 — Smoke + quality gates

- [ ] **Apply the migration locally**: `docker compose exec adcp-server python scripts/ops/migrate.py`.
- [ ] **`make quality`** must pass: format, lint, mypy, all unit tests.
- [ ] **`tox -e integration`** for any code that touches the DB layer
      (repository, sync, persistence path).
- [ ] **Live smoke test against the real platform** with a test account:
      verify token mint, inventory sync, full create→check→delete cycle.
- [ ] **Playwright UI check** if you added the product config UI. Confirm
      every picker populates from the synced cache. Reference:
      `/tmp/playwright-fw-product-config.js` in the FW PR.

---

## Common gotchas

These caught us during FW. Watch for them.

- **Stale uvicorn imports.** If you add a new route to a blueprint, the
  running uvicorn process won't see it until you restart the container
  (`docker compose restart adcp-server`). Code is volume-mounted, but
  Python imports cache at startup.
- **Migration heads.** If you and main both add migrations off the same
  parent, the migration graph has two heads. Fix by editing your migration
  to be a merge revision (`down_revision = ("your_old_parent", "mains_new_head")`).
  Verify with `uv run alembic heads`.
- **Two-stage stale-deps in docker.** After a `uv.lock` bump, use
  `make compose-up` (not bare `docker compose build`) — BuildKit's cache
  mount can short-circuit the install layer otherwise.
- **JSON ID coercion.** JSON gives you strings; ORM Integer PK columns
  need `int`. Cast at the boundary, not after.
- **DeliveryStatus enum mismatch.** AdCP `DeliveryStatus` has 6 values
  (`delivering`, `not_delivering`, `completed`, `budget_exhausted`,
  `flight_ended`, `goal_met`). There is **no** `paused` — map platform
  "paused" to `not_delivering`.
- **DRY across pagination, encryption, fixture-builders.** The duplication
  guard (`check_code_duplication.py`) ratchets — every new copy-paste block
  fails the build. Extract a helper.
- **Inventory cache is NOT exposed to AdCP buyers.** It's a private
  publisher-side aid for the product UI. Buyers discover properties via
  AAO (adagents.json), not your adapter cache.

---

## Phase 11 — Ship

- [ ] PR title uses Conventional Commits prefix (e.g.
      `feat(<name>): full <Platform> API adapter — auth, inventory sync, targeting`).
- [ ] Commits are reviewable in order — auth → client → inventory → product
      config → reporting cache → typed embedder API.
- [ ] PR body has a live-coverage matrix (what's verified against the real
      API today) and what's blocked on platform scope grants.
- [ ] If you found unrelated improvements (e.g. cross-adapter shared
      scheduler), file them as separate issues — don't bundle.

---

## Quick file checklist

| File | What |
|---|---|
| `src/adapters/<name>/__init__.py` | Public exports |
| `src/adapters/<name>/schemas.py` | ConnectionConfig + ProductConfig |
| `src/adapters/<name>/adapter.py` | The AdServerAdapter subclass |
| `src/adapters/<name>/client.py` | API client facade |
| `src/adapters/<name>/_transport.py` | HTTP transport |
| `src/adapters/<name>/targeting.py` | Targeting translation |
| `src/adapters/<name>/formats.py` | Static format declarations |
| `src/adapters/<name>/inventory_sync.py` | Inventory taxonomy walker |
| `src/adapters/<name>/reporting_sync.py` | Reporting sync (may be stub) |
| `src/adapters/__init__.py` | Add to `ADAPTER_REGISTRY` |
| `src/admin/api_schemas/tenant_management.py` | `<Name>AdapterConfig` + discriminated union |
| `src/admin/tenant_management_api.py` | `_adapter_config_to_dict`, `_persist_adapter_config`, catalog metadata |
| `src/admin/blueprints/adapters.py` | test-connection, inventory query, sync endpoints |
| `src/core/database/models.py` | Cache table ORM models |
| `src/core/database/repositories/<name>_*.py` | Tenant-scoped repositories |
| `alembic/versions/<rev>_<name>_*.py` | Cache table migrations |
| `templates/tenant_settings.html` | Picker card |
| `templates/adapters/<name>/connection_config.html` | Connection form |
| `templates/adapters/<name>/product_config.html` | Product targeting form |
| `tests/unit/test_<name>_*.py` | Per-area unit tests |
| `tests/integration/test_<name>_live.py` | Live API smoke (mark `live`) |
| `tests/unit/test_tenant_management_schemas.py` | Typed-config schema tests (extend existing) |
| `tests/integration/test_tenant_management_api_integration.py` | Catalog assertion (extend existing) |
| `docs/adapters/<name>/README.md` | Adapter-specific doc |
| `docs/adapters/README.md` | Index update |
| `docs/api/tenant-management-openapi.{json,yaml}` | Canonical generated API spec, regenerated via `make openapi` |
