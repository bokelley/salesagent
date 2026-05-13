# FreeWheel Adapter

Integrates the Prebid Sales Agent with **Comcast/FreeWheel's Publisher API**
(`api.freewheel.tv`) for video and CTV advertising. Live-verified end-to-end
against the Talpa network (Dutch broadcaster): inventory sync of 2,500+
entities and full create → check → delete cycles for Campaigns, Insertion
Orders, Placements, and Creative Resources.

## Entity mapping (Mapping A)

| AdCP entity | FreeWheel entity |
|---|---|
| MediaBuy | Insertion Order (commercial transaction — budget, schedule, currency, stage) |
| Package | Placement (delivery unit, one per package) |
| Product `implementation_config` | Inventory + targeting selectors (sites, sections, video groups, series, ad-unit packages, audiences, content classification, …) |
| Creative | `creative_resources` (the asset record) |
| Creative-to-package assignment | `creative_instances` *(scope grant pending)* |

A FreeWheel Campaign sits above the IO as a grouping layer; the adapter
auto-creates one Campaign per AdCP MediaBuy. The IO is the unit of commerce;
Placements carry targeting and delivery scope.

## Authentication

Two paths, both supported:

| Path | When to use | TTL |
|---|---|---|
| **OAuth2 password grant** (canonical) | Publisher provides `username` + `password`; adapter mints and refreshes bearers automatically | ~7 days, auto-refreshed |
| **Pre-minted bearer** (`api_token`) | Partner-provisioned token (e.g. ad-hoc testing, test accounts that don't expose user creds) | Caller-managed |

Either path satisfies the connection config — the test endpoint reports
which mode is in use. Credentials are encrypted at rest with Fernet.

## Configuration

### Connection (tenant-level)

Set in **Settings → Ad Server → FreeWheel** in the admin UI, or via the
Tenant Management API.

| Field | Required | Description |
|---|---|---|
| `username` | yes (with password) | FW publisher user — drives password-grant auth |
| `password` | yes (with username) | FW publisher password — encrypted at rest |
| `api_token` | optional escape hatch | Pre-minted bearer; bypasses password grant |
| `environment` | yes | `production` (`api.freewheel.tv`) or `staging` (`api.stg.freewheel.tv`) |
| `default_advertiser_id` | optional | Fallback FW advertiser ID for principals without a `freewheel.advertiser_id` mapping |

The **Test Connection** button validates the bearer against `/auth/token/info`
and reports the auth mode actually used.

### Inventory sync

Once connected, the **Sync Inventory** button populates a local cache of
FreeWheel's inventory taxonomy. The sync walks every Publisher API surface
the adapter consumes and stores the results in the `freewheel_inventory` table:

| Entity | What it is | Source |
|---|---|---|
| `site` | Top-level inventory containers | v4 inventory |
| `site_section` | Section within a site | v4 inventory |
| `site_group` | Cross-site grouping | v4 inventory |
| `series` | Editorial show | v4 inventory |
| `video_group` | Topical grouping of videos | v4 inventory |
| `ad_unit_package` | Bundled ad units (with nested ad_units fetched per package) | v4 inventory |
| `ad_unit_node` | Placement→ad_unit binding | v3 commercial XML |
| `standard_attribute` | All taxonomy axes (genres, dayparts, durations, territories, languages, device types, OSs, environments, stream types, subscription models, addressability, privacy signals, TV ratings, viewership profiles, audiences) | v4 inventory |

The cache is private to the adapter — **not** exposed to AdCP buyers (their
property discovery goes through AAO / adagents.json). It refreshes on demand
via the Sync Inventory button.

### Product (per-product)

Each Product's `implementation_config.freewheel` carries the full FreeWheel
targeting surface. The product setup UI populates every picker from the
synced inventory cache. Eighteen dimensions are exposed:

| Group | Fields |
|---|---|
| **Inventory** | `site_ids`, `site_section_ids`, `video_group_ids`, `series_ids`, `ad_unit_package_id` |
| **Audience** | `viewership_profile_ids`, `audience_item_ids` |
| **Content classification** | `genre_ids`, `content_daypart_ids`, `content_duration_ids`, `content_territory_ids`, `language_ids`, `tv_rating_ids` |
| **Delivery context** | `device_type_ids`, `os_ids`, `environment_ids`, `stream_type_ids`, `subscription_model_ids` |
| **Privacy** | `addressability_ids`, `privacy_signal_ids` |
| **Pricing** | `price_model`, `priority` |
| **Escape hatches** | `targeting_profile_id` (saved FW targeting profile), `custom_targeting` (key-value) |

### Per-package overrides

A package can override the product's custom targeting via
`targeting_overlay.custom["freewheel"]`:

```json
{"custom": {"freewheel": {"genre": ["sports"], "audience": ["enthusiasts"]}}}
```

Package values beat product defaults when both define the same key.

### Principal mapping

Each principal needs `freewheel.advertiser_id` in `platform_mappings`:

```json
{"freewheel": {"advertiser_id": "12345"}}
```

The adapter falls back to `default_advertiser_id` from the connection config
when a principal has no explicit mapping.

## Capabilities

| | |
|---|---|
| **Pricing models** | `cpm`, `flat_rate` |
| **Channels** | `olv`, `ctv`, `display` |
| **Geo targeting** | Country, region, Nielsen DMA |
| **Custom targeting** | Yes (key-value) |
| **Inventory sync** | Yes (entire taxonomy walked into local cache) |
| **AI inventory discovery** | Yes (`get_available_inventory()` reads from cache) |
| **Creative formats** | 6 canonical VAST video formats (15s/30s × pre/mid/post-roll) |
| **Webhooks** | No (FW v4 webhooks endpoint exists; scope grant pending) |
| **Realtime reporting** | No (Query Reporting API is a separate surface; scope grant pending) |

## Targeting translation

AdCP targeting overlays translate into FreeWheel's placement targeting:

| AdCP field | FreeWheel field |
|---|---|
| Product `targeting_profile_id` | `targetingProfileId` |
| `geo_countries` | `geo.countries` |
| `geo_regions` | `geo.regions` |
| `geo_metros` (Nielsen DMA) | `geo.metros` |
| `device_type_any_of` | `deviceTypes` |
| Product `custom_targeting` + package `custom.freewheel` | `customCriteria` |

`geo_postal_areas` is rejected — FreeWheel doesn't expose postal-area
targeting via the Publisher API. Use Nielsen DMA (`geo_metros`) or
`geo_regions` instead.

## Live coverage matrix

| Capability | Status | Notes |
|---|---|---|
| `create_media_buy` | ✅ live | Campaign + IO + Placement(s) cycle verified against Talpa |
| `check_media_buy_status` | ✅ live | Reads IO `stage`/`status` |
| `add_creative_assets` | 🟡 partial | `creative_resources` CRUD verified; `creative_instances` blocked by IAM scope |
| `associate_creatives` | ⏳ blocked | Blocked on `creative_instances` scope |
| `update_media_buy` (pause/resume) | 🟡 client-ready | `update_placement` verified at v3; adapter wiring needs IO-scoped placement listing (scope grant pending) |
| `update_media_buy` (per-package budget) | ❌ data-model | FW budget lives on the IO, not placement — would require a different mapping |
| `get_media_buy_delivery` | ⏳ stub | Needs Query Reporting API (separate surface, scope grant pending) |
| `get_packages_snapshot` | ⏳ stub | Same root cause as `get_media_buy_delivery` |
| `get_available_inventory` | ✅ live | Surfaces synced cache: placements (ad_unit_packages), ad_units (sites + sections), targeting groups, creative specs |
| `get_creative_formats` | ✅ static | 6 canonical VAST video formats |

## Provisioning

There is **no self-serve sandbox**. To get credentials:

1. Have an active FreeWheel commercial relationship.
2. Ask your FreeWheel Account Team to provision either a publisher user
   (username + password) or a pre-minted bearer for server-to-server
   integration.
3. Specify staging vs. production — tokens are environment-scoped.
4. Provide an egress IP if FreeWheel asks for IP allowlisting.

### Scope grants still needed

For a production-grade integration, request these v4 IAM scopes:

**Tier 1 — unblocks core lifecycle:**
- `creative_instances` (write) — binds creatives to placements
- `insertion_orders/{id}/placements` (read) — per-IO placement listing
- `ad_unit_nodes` (write) — placement→ad_unit binding

**Tier 2 — unblocks reporting:**
- `reports` / `insertion_orders/{id}/delivery` (read) — Query Reporting API
  → unblocks both `get_media_buy_delivery` and `get_packages_snapshot`

**Tier 3 — improves operator UX:**
- `targeting_profiles` (read) — attach saved FW targeting to products
- `audiences` + `audience_segments` (read) — richer audience surfacing
- `webhooks` (write) — push state-change notifications, replaces polling

**Tier 4 — future:**
- `forecasts`, `avails`, `inventory_forecast` (read) — pre-buy projections
- `marketplace_deals` / `programmatic` (write) — PMP deal lifecycle

Every probed-but-denied endpoint returns the AWS API Gateway response
`{"Message": "User is not authorized... explicit deny in an identity-based
policy"}`, confirming the surface exists and only an IAM policy update is
needed.

## Constraints

- **No self-serve provisioning.** Users + bearers come from the FW Account Team.
- **Token TTL is ~7 days.** Adapter caches and refreshes proactively.
- **Rate limits.** Auth endpoint: 3 req/sec per IP. API surface: 20 req/sec.
- **Reporting is a separate API surface.** Not on the Publisher API entity
  endpoints — `?fields=delivery` is silently ignored on v3 entities.
  Delivery data lives exclusively on the Query Reporting API.

## Related

- [Adapter README](../README.md) — index and overview
- [Adapter architecture](../../development/architecture.md#adapter-pattern)
- [FreeWheel Authentication API](https://api-docs.freewheel.tv/publisher/docs/authentication-api)
- [FreeWheel Publisher API](https://api-docs.freewheel.tv/publisher/docs)
