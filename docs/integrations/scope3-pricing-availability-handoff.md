# Scope3 Storefront Handoff: Cached Pricing, Availability, and Signal Coverage

Use this prompt after the Sales Agent pricing/availability sync work merges.

```text
Implement storefront support for Sales Agent cached catalog guidance.

Do not call GAM, FreeWheel, SpringServe, or any publisher ad server directly
from the storefront. Treat Sales Agent as the source of cached product
pricing, availability, and signal-coverage guidance.

Product catalog:
- Read get_products normally.
- For each product, read product.forecast when present.
- Read pricing_options[].price_guidance for auction guidance. These are
  guidance percentiles, not floors.
- Read product.forecast.ext.bookable when present. In buyer-facing product
  discovery, prefer bookable products and degrade gracefully when bookability
  is missing.
- Treat forecast.valid_until as freshness metadata. If stale or missing, show
  guidance as unavailable/stale rather than treating values as zero.
- Product-level is the buyer-facing catalog scope. Do not create separate
  storefront products per country or placement unless the seller explicitly
  authors those as products.

Signal catalog:
- Read get_signals normally.
- For mapped signals, read coverage_forecast.
- Use coverage_forecast.points to explain how much inventory a signal retains
  or removes, e.g. present vs not present coverage.
- If coverage_forecast is missing or stale, show “coverage unavailable” and do
  not block all buying.

Sync status and webhooks:
- Poll GET /tenants/{tenant_id}/status for coarse health:
  inventory, reporting, signal_coverage, and pricing_availability.
- Use GET /tenants/{tenant_id}/sync-history for drill-down.
- Subscribe to:
  - product.updated
  - signal.updated
  - sync_run.completed
  - sync_run.failed
- On product.updated or signal.updated, refresh get_products/get_signals.
- On sync_run.failed, use error.category where available to decide whether the
  UI should offer retry, reconnect, or contact-admin guidance.

UX rules:
- Label all values as estimates/guidance unless the API explicitly says fixed.
- Show impression/view/click/complete metrics only when present.
- Do not infer missing views/clicks/completes as zero unless the metric is
  present with mid=0.
- Surface forecast freshness and sync failure status to operators.
```
