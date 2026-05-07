# Triton Digital Adapter

The Triton adapter integrates the Prebid Sales Agent with Triton Digital's
**TAP Media Buying API** (`mbapi.tritondigital.com`) for streaming audio and
podcast advertising.

## How it works

| AdCP entity | TAP entity |
|---|---|
| MediaBuy | Campaign (under your publisher's Advertiser) |
| Package | Flight (with `targetingRules` for inventory selection) |
| Creative | Ad (linked to a Flight) |
| Product | Station / station-group / daypart selection |

Authentication is **publisher-scoped**: a single set of credentials covers
every station owned by your Triton publisher account. Station selection
happens on the flight via `targetingRules` and is configured per-product, not
per-tenant. The adapter exchanges your username + password for a JWT against
`login.tritondigital.com`, caches it, and refreshes automatically on 401.

## Configuration

### Connection (tenant-level)

Set in **Settings → Ad Server → Triton Digital** in the admin UI, or via the
Tenant Management API.

| Field | Description |
|---|---|
| `username` | Publisher login email |
| `password` | Publisher password (stored encrypted with Fernet) |
| `base_url` | TAP Media Buying API URL (default `https://mbapi.tritondigital.com`) |
| `login_url` | TAP Login API URL (default `https://login.tritondigital.com`) |
| `default_advertiser_id` | Fallback TAP advertiser ID for principals without explicit triton mappings |

The password column is encrypted at rest. The admin UI's password field can
be left blank on edit to keep the previously-saved value. The **Test
Connection** button performs a real JWT login and reports the publisher name.

### Product (per-product)

Set on each Product's `implementation_config.triton`:

| Field | Description |
|---|---|
| `station_ids` | TAP station IDs (e.g. `["KROQ", "KIIS"]`) |
| `station_group_ids` | Publisher-defined station bundles |
| `genres` | Station genres (Shoutcast taxonomy: `["Rock", "News", "Sports"]`) |
| `stream_types` | `radio_stream` and/or `podcast` |
| `daypart_ids` | TAP daypart entity IDs |

### Per-package overrides

A package can override the product's station selection through
`targeting_overlay.custom["triton"]`:

```json
{
  "custom": {
    "triton": {
      "station_ids": ["WXYZ"],
      "genres": ["Sports"]
    }
  }
}
```

The package value beats the product default when both are present.

### Principal mapping

Each principal needs a `triton.advertiser_id` in `platform_mappings`:

```json
{
  "triton": {"advertiser_id": "12345"}
}
```

The adapter falls back to `default_advertiser_id` from the connection config
when a principal has no explicit Triton mapping.

## Capabilities

| | |
|---|---|
| **Pricing models** | `cpm`, `flat_rate` |
| **Channels** | `streaming_audio`, `podcast` |
| **Geo targeting** | Country, state, market (Nielsen DMA) |
| **Custom targeting** | Not supported (use product-level genres) |
| **Inventory sync** | Yes (stations endpoint) |
| **Webhooks** | No |
| **Realtime reporting** | No (polled — full reporting wires into TAP report-queue) |

## Targeting translation

AdCP targeting overlays translate into TAP `targetingRules`:

| AdCP field | TAP rule |
|---|---|
| Product `station_ids` | `{type:"in", dimension:"station", values:[...]}` |
| Product `station_group_ids` | `{type:"in", dimension:"station-group", values:[...]}` |
| Product `genres` | `{type:"in", dimension:"station-genre-shoutcast", values:[...]}` |
| `geo_countries` | `{type:"in", dimension:"country", values:[...]}` |
| `geo_regions` | `{type:"in", dimension:"state", values:[...]}` |
| `geo_metros` (Nielsen DMA) | `{type:"in", dimension:"market", values:[...]}` |

Multiple rules combine with AND. Audio-incompatible targeting (CTV device,
non-audio media types, IAB content categories, browser targeting) is rejected
with a clear error before any API call.

## Constraints

- **Direct-sold only.** Triton's Media Buying API covers direct-sold orders.
  Programmatic flows go through TAP Programmatic, a separate API not covered
  by this adapter.
- **Audio-only.** Display, video, and CTV media types are rejected.
- **Live-mode creatives.** Dry-run logs the planned Ad upload + flight
  linking. Real-mode creative upload lands in a follow-up once we validate
  against the Ads endpoint shape on a live publisher account.
- **Live-mode reporting.** Dry-run returns simulated delivery numbers; live
  mode currently returns zeros until the report-queue + poll loop is wired.

## Related

- [Adapter README](../README.md) — index and overview
- [Adapter architecture](../../development/architecture.md#adapter-pattern)
- [Triton TAP Media Buying API docs](https://mbapi.tritondigital.com/doc/)
- [Triton Login API](https://login.tritondigital.com/docs)
