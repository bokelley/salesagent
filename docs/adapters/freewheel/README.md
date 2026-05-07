# FreeWheel Adapter

The FreeWheel adapter integrates the Prebid Sales Agent with **Comcast/FreeWheel's
Publisher API** (`api.freewheel.tv`) for video and CTV advertising.

> **Status:** skeleton-only as of this commit. The adapter is fully wired into
> the registry, factory, admin UI, and tenant management API; OAuth2 token
> exchange and connectivity tests work against real credentials. Live-mode
> create/update/reporting operations are intentionally stubbed (return a clear
> `pending_credentials` error) until staging credentials and sandbox-validated
> JSON shapes are available. Dry-run mode logs the planned API calls based on
> the public Publisher API reference.

## How it works

| AdCP entity | FreeWheel entity |
|---|---|
| MediaBuy | Campaign |
| Package | Line Item |
| Creative | Creative |
| Creative-to-package assignment | Creative-Line Item Association |
| Product | Placement(s) + targeting profile |

Authentication is **OAuth2 `client_credentials`** — server-to-server, no human
login. Tokens minted from your client_id/client_secret have a 7-day TTL; the
adapter caches the token and refreshes automatically on 401.

## Configuration

### Connection (tenant-level)

Set in **Settings → Ad Server → FreeWheel** in the admin UI, or via the
Tenant Management API.

| Field | Description |
|---|---|
| `client_id` | OAuth client ID provisioned by FreeWheel Account Team |
| `client_secret` | OAuth client secret (stored encrypted with Fernet) |
| `network_id` | FreeWheel network identifier (used in API resource paths) |
| `environment` | `production` (`api.freewheel.tv`) or `staging` (`api.stg.freewheel.tv`) |
| `default_advertiser_id` | Fallback FreeWheel advertiser ID for principals without explicit freewheel mappings |

The client_secret column is encrypted at rest. The admin UI's secret field can
be left blank on edit to keep the previously-saved value. The **Test
Connection** button performs a real OAuth token fetch + network record GET and
reports the network name.

### Product (per-product)

Set on each Product's `implementation_config.freewheel`:

| Field | Description |
|---|---|
| `placement_ids` | FreeWheel placement IDs this product targets |
| `targeting_profile_id` | Optional pre-built FreeWheel targeting profile ID |
| `priority` | Line item priority (lower = higher priority) |
| `custom_targeting` | `{key: [values]}` for FreeWheel custom KV targeting |

### Per-package overrides

A package can override the product's custom targeting through
`targeting_overlay.custom["freewheel"]`:

```json
{
  "custom": {
    "freewheel": {
      "genre": ["sports"],
      "audience": ["enthusiasts"]
    }
  }
}
```

Package values beat product defaults when both define the same key.

### Principal mapping

Each principal needs a `freewheel.advertiser_id` in `platform_mappings`:

```json
{
  "freewheel": {"advertiser_id": "12345"}
}
```

The adapter falls back to `default_advertiser_id` from the connection config
when a principal has no explicit FreeWheel mapping.

## Capabilities

| | |
|---|---|
| **Pricing models** | `cpm`, `flat_rate` |
| **Channels** | `olv`, `ctv`, `display` |
| **Geo targeting** | Country, region, market (Nielsen DMA) |
| **Custom targeting** | Yes (key-value) |
| **Inventory sync** | Yes (placements endpoint) |
| **Webhooks** | No |
| **Realtime reporting** | No (separate FreeWheel reporting API, wired in a follow-up) |

## Targeting translation

AdCP targeting overlays translate into FreeWheel's line-item `targeting` object:

| AdCP field | FreeWheel field |
|---|---|
| Product `targeting_profile_id` | `targetingProfileId` |
| `geo_countries` | `geo.countries` |
| `geo_regions` | `geo.regions` |
| `geo_metros` (Nielsen DMA) | `geo.metros` |
| `device_type_any_of` | `deviceTypes` |
| Product `custom_targeting` + package `custom.freewheel` | `customCriteria` |

`geo_postal_areas` is rejected — FreeWheel doesn't expose postal-area targeting
in the Publisher API. Use Nielsen DMA (`geo_metros`) or `geo_regions` instead.

## Provisioning

There is **no self-serve sandbox**. To get credentials:

1. Have an active FreeWheel commercial relationship.
2. Ask your FreeWheel Account Team to provision an OAuth client + network for
   server-to-server integration.
3. Specify whether you need staging or production access — tokens are
   environment-scoped.
4. Provide an egress IP if FreeWheel asks for IP allowlisting (the public docs
   are silent on this but the per-IP rate limit suggests they pin per-IP).

## Constraints

- **No self-serve provisioning.** OAuth clients must be provisioned by the
  FreeWheel Account Team.
- **Token TTL is 7 days.** The adapter caches and refreshes proactively; long
  enough that one provisioning session covers many requests.
- **Rate limits.** Auth endpoint: 3 req/sec per IP. API surface: 20 req/sec.
  The adapter makes one auth call per 7 days under normal use; API calls
  dominate.
- **Live mode is currently stubbed.** Concrete request shapes for
  Campaign/LineItem/Creative POSTs are validated against staging once
  credentials arrive.

## Related

- [Adapter README](../README.md) — index and overview
- [Adapter architecture](../../development/architecture.md#adapter-pattern)
- [FreeWheel Authentication API](https://api-docs.freewheel.tv/publisher/docs/authentication-api)
- [FreeWheel Publisher API](https://api-docs.freewheel.tv/publisher/docs)
