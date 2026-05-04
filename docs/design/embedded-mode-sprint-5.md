# Sprint 5 Spec: Remaining Publisher-Managed Sub-Resources via API (Optional)

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1](./embedded-mode-sprint-1.md), [sprint 1.5](./embedded-mode-sprint-1.5.md), [sprint 2](./embedded-mode-sprint-2.md), [sprint 3](./embedded-mode-sprint-3.md), [sprint 4](./embedded-mode-sprint-4.md)
**Status:** Draft, optional
**Last updated:** 2026-05-04

## Scope

Sprint 5 fills out the remaining publisher-managed sub-resources via API. Like sprint 4, this is **optional** — these surfaces are editable via the proxied UI, so API endpoints are an automation convenience, not a prerequisite for Scope3's launch. Build this only if there's a concrete need to manage these entities programmatically.

After sprint 5 (combined with sprint 4), the API surface is feature-complete for every config knob the publisher can set in the UI — Scope3 can automate or bulk-edit anything if it wants.

20 endpoints across 7 sub-resource groups:

```
# Property tags
GET     /tenants/{tid}/property-tags
POST    /tenants/{tid}/property-tags
DELETE  /tenants/{tid}/property-tags/{tag_id}

# Authorized properties (publishers' verified inventory ownership)
GET     /tenants/{tid}/authorized-properties
POST    /tenants/{tid}/authorized-properties           # manual single-create
POST    /tenants/{tid}/authorized-properties/bulk      # CSV / list import
DELETE  /tenants/{tid}/authorized-properties/{pid}
POST    /tenants/{tid}/authorized-properties/{pid}/verify

# Inventory profiles
GET     /tenants/{tid}/inventory-profiles
POST    /tenants/{tid}/inventory-profiles
PATCH   /tenants/{tid}/inventory-profiles/{pid}
DELETE  /tenants/{tid}/inventory-profiles/{pid}

# Currency limits
GET     /tenants/{tid}/currency-limits
PUT     /tenants/{tid}/currency-limits

# Slack config
GET     /tenants/{tid}/slack-config
PUT     /tenants/{tid}/slack-config
POST    /tenants/{tid}/slack-config/test

# Business rules
GET     /tenants/{tid}/business-rules
PUT     /tenants/{tid}/business-rules

# Policy
GET     /tenants/{tid}/policy
PUT     /tenants/{tid}/policy

# Creative agents
GET     /tenants/{tid}/creative-agents
POST    /tenants/{tid}/creative-agents
PATCH   /tenants/{tid}/creative-agents/{aid}
DELETE  /tenants/{tid}/creative-agents/{aid}
POST    /tenants/{tid}/creative-agents/{aid}/test

# Signals agents
GET     /tenants/{tid}/signals-agents
POST    /tenants/{tid}/signals-agents
PATCH   /tenants/{tid}/signals-agents/{aid}
DELETE  /tenants/{tid}/signals-agents/{aid}
POST    /tenants/{tid}/signals-agents/{aid}/test
```

All publisher-managed (UI also covers these). Same plumbing as sprints 1-2: spectree, Pydantic, management-API-key auth, no model write-guard interference.

## Pattern: shared business logic with the UI

This sprint formalizes the convergence pattern (see parent design's "UI ↔ API mapping" section): for each new API endpoint, extract a repository / `_impl()` function and refactor the corresponding UI handler to call it. By end of sprint 5, the publisher-managed UI domain has converged on this shape across all sub-resources.

Per-domain extraction list (existing UI handler → new shared function):

| Sub-resource | UI blueprint | New repository / impl |
|---|---|---|
| Property tags | `authorized_properties.py` (mixed) | `src/core/repositories/property_tag_repository.py` |
| Authorized properties | `authorized_properties.py` | `src/core/repositories/authorized_property_repository.py` |
| Inventory profiles | `inventory_profiles.py` | `src/core/repositories/inventory_profile_repository.py` |
| Currency limits | `settings.py` (within `/general` PATCH) | `src/core/services/_set_currency_limits_impl()` |
| Slack config | `settings.py` (within `/slack` PATCH) | `src/core/services/_set_slack_config_impl()` + existing `_test_slack_impl()` |
| Business rules | `settings.py` (within `/business-rules` PATCH) | `src/core/services/_set_business_rules_impl()` |
| Policy | `policy.py` | `src/core/services/_set_policy_impl()` |
| Creative agents | `creative_agents.py` | `src/core/repositories/creative_agent_repository.py` + `_test_creative_agent_impl()` |
| Signals agents | `signals_agents.py` | `src/core/repositories/signals_agent_repository.py` + `_test_signals_agent_impl()` |

The opportunistic refactor of existing UI handlers happens alongside each API endpoint's implementation. Both transports call the same function; the UI handler converts request.form → repo call → render template; the API endpoint converts JSON → repo call → JSON out.

## Pydantic schemas (representative samples)

Schemas live in `src/admin/api_schemas/{property_tags,authorized_properties,...}.py`. Most follow the same pattern as sprints 1-2; examples below illustrate the parts worth design attention.

### Property tags (simple)

```python
class PropertyTagCreateRequest(BaseModel):
    tag_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None

class PropertyTagSummary(BaseModel):
    tag_id: str
    name: str
    description: str | None
    property_count: int  # how many authorized properties carry this tag
    is_default: bool     # e.g., the all_inventory tag created at provision time
    created_at: datetime
```

`tag_id` is user-supplied with character constraints (it's referenced in product configs). Default tags (`all_inventory`) cannot be deleted — DELETE returns 409 `default_tag_protected`.

### Authorized properties (CSV/bulk + verification)

This is the meatier sub-resource. Existing UI supports manual create, CSV upload, sync-from-adagents.txt, and per-property verification. The API surfaces all four:

```python
class AuthorizedPropertyCreateRequest(BaseModel):
    domain: str = Field(..., max_length=255)
    property_type: Literal["website", "app", "ctv"] = "website"
    property_tags: list[str] = Field(default_factory=list)  # tag_ids
    notes: str | None = None

class AuthorizedPropertyBulkRequest(BaseModel):
    """Bulk import — accepts either an explicit list or a CSV body."""
    properties: list[AuthorizedPropertyCreateRequest] | None = None
    csv_data: str | None = None  # if set, parse as CSV with columns matching the create schema
    upsert: bool = False         # if true, existing rows with the same domain are updated; else 409 on conflict

    @model_validator(mode="after")
    def exactly_one_input(self):
        if (self.properties is None) == (self.csv_data is None):
            raise ValueError("Provide either properties[] or csv_data, not both")
        return self

class BulkImportResultItem(BaseModel):
    domain: str
    status: Literal["created", "updated", "skipped_existing", "failed"]
    property_id: str | None
    error: str | None

class BulkImportResponse(BaseModel):
    total: int
    created: int
    updated: int
    skipped: int
    failed: int
    items: list[BulkImportResultItem]

class VerifyAuthorizedPropertyResponse(BaseModel):
    property_id: str
    domain: str
    verified: bool
    verification_method: str   # e.g., "ads.txt", "adagents.txt"
    error: str | None
    verified_at: datetime
```

`POST /authorized-properties/{pid}/verify` triggers the verification check synchronously (typically <2s). For network failures or timeouts, returns 504 with the property left unverified — Scope3 retries.

`POST /authorized-properties/bulk` is synchronous with a 60s timeout; for very large imports (>1000 rows), Scope3 chunks. Per-row failures collected in `items[]` rather than rolling back the whole import — same pattern as sprint 2's `autogenerate-from-gam`.

### Currency limits (singleton sub-resource, PUT replaces)

```python
class CurrencyLimit(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3)  # ISO 4217
    enabled: bool = True
    min_budget: Decimal | None = None
    max_budget: Decimal | None = None

class CurrencyLimitsRequest(BaseModel):
    """PUT replaces the entire set."""
    limits: list[CurrencyLimit]

    @model_validator(mode="after")
    def must_include_default_currency(self):
        # USD (or whatever the tenant's default_currency is) cannot be removed.
        # The repository validates this against the tenant.
        return self
```

PUT semantics: the request *is* the new state. Currencies not in the list are removed. The tenant's default currency cannot be removed (400 `default_currency_required`).

### Agents (creative + signals follow the same shape)

```python
class CreativeAgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: Literal["mcp", "a2a", "http"]
    endpoint_url: str
    auth: AgentAuthConfig    # discriminated union: bearer / api_key / oauth / none
    capabilities: list[str]  # which creative ops the agent supports
    enabled: bool = True

class TestAgentResponse(BaseModel):
    success: bool
    latency_ms: int | None
    error: str | None
    capabilities_advertised: list[str] | None  # what the agent claims it supports
    tested_at: datetime
```

`POST /{agent}/test` calls the agent's discovery/health endpoint with the configured auth. Used by Scope3 to validate connection before saving and by health-check automation.

## Endpoint behavior (per group, briefly)

Following the same patterns as sprint 2; the table summarizes what's noteworthy per group rather than enumerating every endpoint.

| Group | Notable behavior |
|---|---|
| Property tags | DELETE blocks default tags (409 `default_tag_protected`). Tags referenced by products: 409 `tag_in_use_by_products` with `details.product_ids`. |
| Authorized properties | `verify` is sync, 504 on timeout. `bulk` accepts CSV or list, sync, per-row results. |
| Inventory profiles | Profiles referenced by active media buys: DELETE returns 409 `profile_in_use`. |
| Currency limits | PUT-replaces semantics. Default currency required. |
| Slack config | `PUT` validates webhook URL format. `POST /test` posts a test message; returns 400 `slack_test_failed` with the Slack error payload. |
| Business rules | PUT-replaces. Schema follows existing `BusinessRules` model. |
| Policy | PUT-replaces. Schema follows existing `Policy` model. |
| Creative agents | `POST /test` calls agent discovery. Active agents in use by products: DELETE returns 409 `agent_in_use`. |
| Signals agents | Same shape as creative agents. |

## Error responses

Reuses sprint 1's `ApiError`. New error codes introduced in sprint 5:

| HTTP | code | When |
|---|---|---|
| 400 | `default_currency_required` | PUT currency-limits without the tenant's default currency |
| 400 | `slack_test_failed` | Slack test post returned non-200 |
| 409 | `default_tag_protected` | DELETE on a default property tag |
| 409 | `tag_in_use_by_products` | Property tag referenced by products |
| 409 | `profile_in_use` | Inventory profile referenced by active media buys |
| 409 | `agent_in_use` | Creative/signals agent referenced by active products |
| 409 | `domain_exists` | Authorized property with same domain (when `upsert=false`) |
| 504 | `verification_timeout` | ads.txt / adagents.txt fetch exceeded timeout |

## Acceptance criteria

**Schemas:**
- [ ] All Pydantic schemas validate happy-path + each documented failure mode.
- [ ] `AuthorizedPropertyBulkRequest` requires exactly one of `properties` or `csv_data`.
- [ ] `CurrencyLimitsRequest` rejects PUT that would remove the default currency.

**Per-domain CRUD:**
- [ ] Each group's CRUD endpoints round-trip cleanly (create, read, update where applicable, delete).
- [ ] Deletes respect referential constraints (tags-by-products, profiles-by-buys, agents-by-products) with 409 + identifying detail.
- [ ] PUT-replace semantics for currency-limits, slack-config, business-rules, policy: removed items are actually removed.

**Bulk authorized properties:**
- [ ] `bulk` accepts both `properties[]` and `csv_data` modes.
- [ ] CSV mode parses headers correctly and rejects malformed rows with item-level errors.
- [ ] `upsert=true` updates existing domain rows; `upsert=false` reports `skipped_existing`.
- [ ] Per-row failures don't roll back successful inserts.

**Verification & tests:**
- [ ] `POST /authorized-properties/{pid}/verify` writes verification state synchronously.
- [ ] `POST /slack-config/test`, `POST /creative-agents/{aid}/test`, `POST /signals-agents/{aid}/test` return diagnostic info on failure (not just `success: false`).

**UI/API convergence:**
- [ ] For each sub-resource group: the existing UI handler and the new API endpoint both call the same repository / impl function. Verified by (a) test that exercises both paths against the same mock repository, (b) grep showing no inline `session.add()` in the touched UI handlers.
- [ ] Existing UI behavior unchanged after refactor (manual smoke + integration tests).

**OpenAPI:**
- [ ] All 20 endpoints listed in the spec.
- [ ] Swagger UI executable for every endpoint.

**Integration with sprints 1-2:**
- [ ] Full provisioning flow: provision tenant → autogenerate products → import authorized properties via bulk → configure slack/creative agents → all via API only.
- [ ] After full convergence: structural-guard violation count for the touched UI blueprints decreases (allowlist shrinks, doesn't grow).

## Open questions

1. **Existing UI handler behavior preservation.** Each refactor must leave the UI's behavior pixel-identical. Worth pinning down acceptance via integration/admin tests touching each sub-resource UI page before refactoring.
2. **CSV parsing strictness.** Match pandas-style permissive parsing (existing UI behavior) or strict RFC 4180? Existing UI uses Python's `csv` module with default settings — replicate exactly to avoid surprises.
3. **Authorized property verification queue.** Sprint 5 ships sync verification (one property per call). For tenants with hundreds of properties, periodic background re-verification is desirable — punt to a follow-up.
4. **Agent capabilities discovery format.** What does "calling agent discovery" actually look like? Likely a GET on the agent's well-known endpoint returning a capabilities manifest. Confirm by reading existing agent test code in `creative_agents.py` and `signals_agents.py`.
5. **Per-tenant policy schema stability.** The `Policy` model exists today. Is it stable enough to expose as PUT-replaces, or are there nested fields that warrant sub-resources of their own (e.g., `/policy/rules`)? Keep PUT-replaces for sprint 5; refactor to sub-resources later if Scope3 finds it awkward.

## What sprint 6 builds on this

Sprint 5 closes out the optional publisher-managed API surface. The only remaining sprint is [sprint 6](./embedded-mode-sprint-6.md) (also optional) — outbound webhooks for state changes, replacing polling load on `GET /status` and `GET /workflows`.
