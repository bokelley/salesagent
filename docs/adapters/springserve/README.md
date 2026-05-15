# SpringServe Adapter

Connect the Prebid Sales Agent to Magnite's SpringServe ad server for
direct-sold CTV, online video, and audio inventory.

> **Why direct-to-ad-server (not via the SSP)?** Magnite runs an AdCP seller
> agent on the Magnite SSP. Routing direct-sold campaigns through the SSP
> agent imposes SSP fees. This adapter talks to SpringServe's ad-server API
> directly, preserving the publisher's direct-sold economics. The first
> production customer is Talpa (Netherlands) for audio inventory across
> Radio 538 / Sky Radio / Radio 10, with video expansion across the
> SBS6 / Net5 / Veronica portfolio as the strategic priority.

## Status

Stage 1 (skeleton + auth + dry-run) ships in this commit. Subsequent stages:

| Stage | Goal | Status |
|---|---|---|
| 1 | Skeleton + auth + dry-run | ✅ this commit |
| 2 | Live Campaign + Demand Tag create | ⏳ next |
| 3 | Creatives (incl. audio MIME negotiation) | ⏳ |
| 4 | Reporting cache + sync | ⏳ |
| 5 | Inventory cache + admin UI + typed embedder config | ⏳ |

See `.context/springserve-adapter-plan.md` for the full plan, risks, and
open questions.

## Entity mapping (Mapping A)

| AdCP entity | SpringServe entity | Endpoint |
|---|---|---|
| MediaBuy | Campaign | `POST /api/v0/campaigns` |
| Package | Demand Tag | `POST /api/v0/demand_tags` (one per package, `campaign_id` parent) |
| Creative (asset) | Video / Audio Creative | `POST /api/v0/videos` (MP4 upload OR remote URL) — OR — VAST tag URL on the demand tag |
| Targeting | Demand-tag fields | `PATCH /api/v0/demand_tags/{id}` |
| Delivery / reporting | Reporting API | `POST /api/v0/report` |
| Inventory taxonomy | Supply Tags + Supply Partners | `GET /api/v0/supply_tags`, `GET /api/v0/supply_partners` |

SpringServe has no "Insertion Order" layer above Campaign — the Campaign IS
the buy. We do not synthesise one.

## Authentication

Two paths, exactly one required:

1. **Email + password (canonical).** Set `email` + `password` in the
   adapter config. The transport mints a token at `POST /api/v0/auth` on
   first use, caches it with a 2-hour TTL, and refreshes on 401 or expiry.

2. **Pre-minted API token (escape hatch).** Set `api_token` in the adapter
   config. Useful when a partner provides a token out-of-band. No
   auto-refresh — rotate manually when the 2-hour TTL expires.

> SpringServe uses the raw token in the `Authorization` header — NOT
> `Authorization: Bearer <token>`. The transport handles this correctly;
> don't try to "fix" it.

## Capabilities

- **Pricing models:** CPM, FLAT_RATE
- **Channels:** OLV, CTV, streaming audio, podcast
- **Targeting:** geo countries / regions / DMAs, device types, player sizes,
  environments, supply-tag inclusion (postal targeting NOT supported —
  use DMAs or regions)
- **Delivery measurement:** SpringServe-native

## Audio support

Audio is a first-class concern on SpringServe — Magnite's iHeartMedia
broadcast / streaming / podcast marketplace runs on the same demand-tag
API surface as video, with audio MIME types (`audio/mp4`, `audio/mpeg`,
≤500 MB) on the creative records. The adapter does not bifurcate; one
SpringServe connection handles both, with the AdCP `Format.type`
discrimination (`video` vs `audio`) driving creative MIME negotiation.

## Scope coverage (Stage 1 live probe, 2026-05-14)

Token mint succeeds; per-endpoint scope on the operator's test account:

| Endpoint | Status | Verdict |
|---|---|---|
| `POST /auth` | ✅ 200 | Token mint works (2-hour TTL) |
| `GET /campaigns` | ✅ 200 | Stage 2 unblocked |
| `GET /demand_tags` | ✅ 200 | Stage 2 unblocked |
| `GET /videos` | ✅ 200 | Stage 3 unblocked |
| `GET /supply_tags` | ❌ 403 | **Stage 5 blocked — request supply-side read scope from SpringServe support** |
| `GET /supply_partners` | ❌ 403 | **Stage 5 blocked — same scope grant unblocks both** |
| `GET /report` | ⏳ 404 | POST-only endpoint; probe shape replaced with a real POST in Stage 4 |

The demand-side write paths (Campaigns, Demand Tags, Videos) are all
unblocked, so Stage 2 (live create_media_buy against a Talpa test account)
can proceed without any further scope asks. Stage 5 (inventory cache) is
gated on a single scope-grant ticket with SpringServe.

## Rate limits

SpringServe enforces 240 req/min per account on the general API and 10 req/min
on the Reporting API. The transport surfaces 429 as `SpringServeRateLimitError`;
the inventory and reporting sync jobs (Stages 4–5) will respect these limits.
