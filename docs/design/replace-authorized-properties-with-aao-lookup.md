# Replace Authorized Properties with AAO Lookup

**Status:** Partially superseded — `Tenant.house_domain` was dropped in PR #78
(May 2026). Per-publisher houses (`PublisherPartner.publisher_domain`) are now
the only "house" concept. References below to `Tenant.house_domain` are
historical.
**Owner:** Sales Agent
**Last updated:** 2026-05-04
**Related:** [embedded-mode](./embedded-mode.md), [sync-accounts-advertiser-mapping.md](./sync-accounts-advertiser-mapping.md)

## Problem

Today's `AuthorizedProperty` table requires every publisher to manually maintain a list of every website / app / CTV property they want their salesagent to represent. Setup checklist gates onboarding on this list being populated, which means a new tenant's first experience is "you're missing 47 properties." That's the wrong first step.

The AAO model is simpler: a publisher publishes one canonical `brand.json` at a `.well-known/` path on their house domain, listing all their properties. They publish `adagents.json` listing which agents are authorized to sell on their behalf. Every other system just *looks these up* — no replication, no manual sync, no stale cache.

The salesagent only needs to know two things to participate in this model:

1. **`Tenant.house_domain`** — where the publisher's `brand.json` lives (`https://{house_domain}/.well-known/brand.json`). The list of properties is whatever's in that file at request time.
2. **`Tenant.public_agent_url`** — the agent URL that publishers list in their `adagents.json`. For embedded-mode tenants this is the host product's stable agent URL (e.g., `https://interchange.io` for the Scope3 reference deployment). For self-hosted publishers it's their own salesagent's URL.

Everything else — the property list, the verification, the partner roster — gets looked up via the AAO SDK on demand. No `AuthorizedProperty` table, no per-property setup form, no "add 47 sites" gate.

## Proposed shape

### Schema

```
Tenant.house_domain:       str | None  (new — required for new tenants)
Tenant.public_agent_url:   str | None  (new — required for new tenants)
```

Existing tables:

- `PublisherPartner` — **stays as-is**. It already tracks "publisher domains we have a relationship with" + adagents.json verification status. It's the right shape for the partnership graph.
- `AuthorizedProperty` — **deprecated**. Old tenants keep their rows; new tenants don't write to it; reads route through the AAO lookup with a fallback to the cached rows for the deprecation window. Migration removes it once usage drops to zero (target: 90 days).
- `PropertyTag` — **stays**. Tags reference is independent of how property records are sourced; embedded-mode tenants still want logical groupings like "all_inventory" or "us_traffic_only".

### Onboarding

The setup checklist's first task becomes:

```
Required to start:
  ✓ Publisher house domain   (e.g., wonderstruck.com)
  ✓ Public agent URL         (e.g., https://interchange.io)
```

Both fields are required — without them the salesagent can't fetch brand.json or verify adagents.json. The Tenant Management API's `POST /tenants/provision` accepts both as request fields; embedded-mode tenants get them at provision time from the host product, open-instance tenants fill them in via the Admin UI.

The "Authorized Properties" task on the setup checklist is replaced with a **brand.json reachability probe**: hit `https://{house_domain}/.well-known/brand.json`, validate it parses, count properties — green if it works, yellow with a hint if it doesn't ("we couldn't fetch your brand.json at the configured house_domain — see [doc link]").

The "Verified Publishers" task stays — it already drives off `PublisherPartner.is_verified` which is the adagents.json check.

### `list_authorized_properties` AdCP tool

Today this reads from `AuthorizedProperty`. New behavior:

```python
async def list_authorized_properties(...):
    house_domain = tenant.house_domain
    if not house_domain:
        # Open-instance tenant pre-migration → fall back to AuthorizedProperty cache
        return _list_from_db_cache(tenant_id)

    # Managed or post-migration tenant → live AAO fetch with short TTL cache
    return await aao_sdk.fetch_brand_properties(house_domain, ttl_seconds=300)
```

The AAO SDK already does the brand.json fetch + parse; salesagent doesn't reinvent that.

5-minute server-side cache absorbs hot-loop calls without making the publisher's CDN our pet — `brand.json` doesn't change often.

### Verification (adagents.json)

For each row in `PublisherPartner`:

```python
async def verify_partner(tenant, partner):
    adagents = await aao_sdk.fetch_adagents_json(partner.publisher_domain)
    partner.is_verified = tenant.public_agent_url in adagents.authorized_agents
    partner.last_synced_at = now()
    partner.sync_error = None if partner.is_verified else "agent_url not listed"
```

Run on a 6-hour cadence (matches existing `sync_all_tenants.py`). Manual "Verify now" button in the Admin UI for impatient publishers.

## Migration plan

Three migrations + a deprecation window:

1. `add_house_domain_and_public_agent_url_to_tenant` — both columns, nullable. Existing tenants get `NULL` defaults. New tenants must populate both at provision time (validation in the Tenant Management API).

2. `seed_house_domain_from_subdomain_for_existing_tenants` — for tenants with `Tenant.virtual_host` or `Tenant.subdomain` set, best-effort populate `house_domain` from those. Manual review for any that don't auto-resolve. **Optional — could just leave them NULL and prompt on next admin login.**

3. `drop_authorized_property_table` — fires after the 90-day deprecation window once all live tenants have populated `house_domain` and the new code path is the default. Tracked as a follow-up issue, not in this sprint.

### Code changes

- `src/admin/blueprints/authorized_properties.py` (~700 LOC) → deletable after the deprecation window. During the window: leave the routes mounted but mark them deprecated in the page header ("This page is deprecated. Move your property list to a brand.json on `{tenant.house_domain}` — see [doc link].").
- `src/services/setup_checklist_service.py` — replace the `AuthorizedProperty.count > 0` gate with the `brand.json` reachability probe. Wire the two new tenant fields as the first two checklist items.
- `src/core/tools/list_authorized_properties.py` (or wherever the AdCP tool lives) — branch on `tenant.house_domain` per the snippet above.
- New: `src/services/aao_lookup_service.py` — wraps the AAO SDK with the salesagent's caching policy.

### Tenant Management API surface

`POST /api/v1/tenant-management/tenants/provision` accepts:

```json
{
  "name": "...",
  "external_org_id": "...",
  "external_source": "scope3",
  "contact_email": "...",
  "house_domain": "wonderstruck.com",
  "public_agent_url": "https://interchange.io",
  "adapter": {...}
}
```

Both fields required for embedded-mode provision (where the host product already knows them); optional for the legacy open-instance create endpoint until existing tenants migrate.

Add `PATCH /tenants/{tid}` support for both fields so publishers can update them via the Admin UI without touching the database directly.

## Acceptance criteria

- [ ] `Tenant.house_domain` and `Tenant.public_agent_url` exist as nullable columns; migration applied.
- [ ] `POST /tenants/provision` (embedded-mode) requires both fields and rejects requests missing either.
- [ ] Setup checklist for a new tenant lists "Publisher house domain" + "Public agent URL" as the **first two** items, before any other onboarding step.
- [ ] `list_authorized_properties` for a tenant with `house_domain` set returns properties from a live `brand.json` fetch (with 5-minute cache).
- [ ] `list_authorized_properties` for a tenant with `house_domain=NULL` falls back to the `AuthorizedProperty` cache (deprecation-window behavior).
- [ ] `PublisherPartner.is_verified` flips based on whether `tenant.public_agent_url` is listed in the partner's `adagents.json`.
- [ ] Admin UI "Authorized Properties" page shows a deprecation banner pointing at the new flow.
- [ ] No new tenant can be provisioned without both fields.

## Open questions

1. **Where does the AAO SDK actually live?** Is it part of `adcp` or is it a separate package we need to vendor? If it's the former, what's the Python API surface for `fetch_brand_properties()` and `fetch_adagents_json()`?
2. **`brand.json` schema authority.** Are we strictly conforming to the AAO spec? If the publisher's `brand.json` has fields we don't yet model (e.g., a property type we haven't added to `PROPERTY_TYPES`), do we drop them silently or surface a warning?
3. **Partial-failure UX.** If `brand.json` fetch fails (404, timeout, malformed JSON), do we (a) return whatever's cached in `AuthorizedProperty`, (b) return an empty list, (c) raise? My instinct is (a) for the deprecation window then (b) after — keeps the buyer protocol responsive without lying about availability.
4. **`public_agent_url` for self-hosted open-instance tenants.** Today they don't have a "public" URL distinct from their salesagent's own deployment URL. Probably default to `get_sales_agent_url()` if they don't set it explicitly.

## Sprint placement

Roughly the same scope as Sprint 1.6: a migration + ~3 code changes + Admin UI deprecation banner. Recommend slotting as **Sprint 1.7** since it's an embedded-mode onboarding requirement (a tenant can't list_authorized_properties until they have these fields), and the first embedded-mode tenants will hit it the moment they try to ship a media buy.

Estimated scope: ~2 days.
- 0.5d migration + Tenant Management API field validation.
- 0.5d Setup checklist refactor.
- 0.5d `list_authorized_properties` branching + AAO lookup service.
- 0.5d Admin UI deprecation banner + tests.
