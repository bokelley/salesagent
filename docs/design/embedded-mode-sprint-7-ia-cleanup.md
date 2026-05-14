# Sprint 7 Spec: IA Cleanup — Tenant Settings vs Configure

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [Sprint 4 (UI hardening)](./embedded-mode-sprint-4-ui-hardening.md), [Sprint 5 (Buyer Routing UX)](./embedded-mode-sprint-5-buyer-routing-ux.md)
**Status:** Draft. Phase 1a + 1b cleanup landed; Phases 2–4 not yet started.
**Last updated:** 2026-05-14

## Why this sprint exists

The admin UI has two parallel configuration surfaces that drifted apart over the prior sprints:

1. **Top-bar `Configure` dropdown** — added during Sprint 5 as the umbrella for standalone configuration pages (Inventory: Browse / Profiles / Targeting / Sync; Buying: Buyer routing; Delivery: Webhooks; Workspace: Settings).
2. **`Settings` page (`/tenant/<id>/settings`)** — the original kitchen-sink tenant-config page with its own internal sidebar nav (Account, Ad Server, Policies & Workflows, Integrations, Publishers, Products, Inventory, Buyer Agents, Signing Keys, Danger Zone). Two entries (Setup Checklist, Users & Access) are already standalone-page links inside the sidebar.

Settings is **one entry inside Configure**, not a peer. But Settings still contains a half-dozen in-page sections that conceptually belong as Configure peers — and several of them duplicate concepts that already have a peer entry (e.g., Settings → Inventory vs Configure → Inventory; Settings → Buyer Agents vs Configure → Buyer Routing). The result is two competing IAs glued together: Configure peers for things promoted out of Settings, Settings sub-sections for things that never were.

Sprint 5 made this worse by half-promoting features (advertiser↔buyer-agent mapping moved from Settings → Advertisers to Configure → Buyer Routing) without removing the old surface — see `templates/tenant_settings.html:2096-2109` for the in-app "Advertiser mapping moved to Buyer Routing" banner that hints at the unfinished move.

This sprint completes the move: **everything that's an entity or distinct workflow becomes a peer page under Configure; Settings shrinks to just tenant-identity config and is hidden entirely on embedded tenants** (because tenant-identity is platform-managed in embedded mode).

## The endgame

```
Primary nav:  Dashboard | Media Buys | Products | Creatives | Workflows | Reports
Configure ▼
  Setup
    └─ Setup Checklist                       (standalone today)
  Inventory
    └─ Browse                                (standalone today)
    └─ Inventory Profiles                    (standalone today)
    └─ Targeting Criteria                    (standalone today)
    └─ Sync                                  (standalone today, hidden on embedded)
  Buying
    └─ Buyer Routing                         (standalone today)
  Delivery
    └─ Webhooks                              (standalone today)
  Workspace
    └─ Publishers                            (promoted by Phase 2)
    └─ Users & Access                        (standalone today)
    └─ Signing Keys                          (promoted by Phase 2)
    └─ Policies & Workflows                  (promoted by Phase 2)
    └─ Integrations                          (promoted by Phase 2)
    └─ Tenant Settings    ← hidden on embedded
```

Sections that **leave Settings entirely** (folded into existing primary-nav pages, not promoted):
- **Products** (Settings sub-section) → fold into primary-nav `Products` page settings tab. The sub-section today is just "default product config"; Products page is the canonical surface.
- **Inventory** (Settings sub-section) → fold into Configure → Inventory group; the sub-section overlaps directly with the four existing Inventory peers.

Sections that **stay in Tenant Settings** (genuine tenant-identity config, all already gated on `not embedded_view`):
- Account (tenant name, subdomain, billing email)
- Ad Server (GAM credentials, network code, refresh token)
- Danger Zone (delete/deactivate)

Sections that **disappear** entirely (already hidden in embedded; redundant on standalone):
- Buyer Agents — Sprint 5 promoted advertiser↔buyer-agent mapping to Buyer Routing; the remaining Principal-admin (access tokens) can move to a small standalone "API Tokens" page under Workspace, or fold into Users & Access. Phase 2 decides.

## Why hide Tenant Settings entirely on embedded

After Phases 2–4, the three sections that remain in Tenant Settings (Account, Ad Server, Danger Zone) are all already individually `{% if not embedded_view %}` gated — they're tenant-identity config that the upstream platform owns via the Tenant Management API. Once those three are the only contents, hiding the page entrypoint in the Configure menu is the natural completion: no entry → no broken-feeling page where 100% of the content is "your platform manages this." It also removes the Phase 1a Buyer-Agents-style bug where stale internal links (e.g., setup checklist actions, in-app banners) point to a section that no longer renders.

## Phasing

This sprint ships in four phases, each independently mergeable. The order minimizes blast radius: small UI-text fixes first, entity promotions middle, fold-ins last.

### Phase 1a — Tenant Settings rename + Buyer Agents hide on embedded ✅ LANDED

**Shipped in PR `bokelley/embedded-buyer-agents-visibility`** — 2026-05-14.

Changes:
- `templates/base.html` — Configure menu entry renamed `Settings` → `Tenant Settings`.
- `templates/tenant_settings.html` — sidebar `Buyer Agents` nav tab and the entire `<div id="advertisers">` section gated on `{% if not embedded_view %}`.
- `tests/integration/test_embedded_ui_hardening.py` — `TestAdvertisersDirectoryReadOnlyOnEmbedded` → `TestAdvertisersDirectoryHiddenOnEmbedded`; flipped three assertions from visible-on-embedded to hidden-on-embedded; new docstring records the Sprint 7 rationale.
- `docs/design/embedded-mode-sprint-4-ui-hardening.md` — header status line marked partially superseded; new "Settings → Advertisers reversal (Sprint 7)" section at the end explaining why the Sprint 4 "read-only directory stays visible" call is reversed.

This reversed the Sprint 4 "the read-only directory stays visible permanently" call. Justification: Sprint 5 made Buyer Routing the canonical home for advertiser↔buyer-agent mapping, so the Settings → Buyer Agents tab on embedded became duplicate read-only data plus an informational banner — noise rather than a useful surface.

### Phase 1b — Audit cleanup ✅ LANDED

Shipped in the same PR. Catches Phase-1a-introduced inconsistencies in surfaces that link into the now-hidden Buyer Agents tab:

- `src/services/setup_checklist_service.py` — `principals_created` task skipped on embedded tenants in both `_check_critical_tasks` and `_build_critical_tasks`. Principal provisioning on embedded is platform-managed (Tenant Management API), so the task is not actionable by the publisher operator, and its `action_url` pointed at the now-hidden `/settings#advertisers` anchor.
- `tests/integration/test_setup_checklist_service.py` — new `TestSprint7PrincipalsCreatedHideOnEmbedded` class with two regression tests.

Surfaces left alone (deliberately not in scope for Phase 1b):

- `src/admin/services/tenant_status_service.py:_PLATFORM_KEYS_WHEN_MANAGED` — the `/status` JSON envelope still tags `principals_created` as `publisher` scope on embedded. Morally it should be `platform` on embedded, but `/status` is the external contract Storefront consumes. A scope-flip needs coordinated rollout. Tracked as Phase 1c (below) if/when needed.
- `templates/add_product.html:201` and `templates/add_product_gam.html:273` — "Add a buyer agent in Settings → Buyer Agents before restricting product access" copy is correct. It only renders in the `{% else %}` branch (not embedded AND no principals exist); on embedded the upper branch renders the right thing.
- `templates/tenant_settings.html:2115` — "This page (Settings → Buyer Agents) remains for managing the …" is now dead code on embedded (the whole `<div id="advertisers">` is hidden), but still accurate copy on standalone. Will be removed in Phase 4 when the section is folded out entirely.

### Phase 1c — `/status` envelope scope-flip (deferred)

Add `principals_created` to `_PLATFORM_KEYS_WHEN_MANAGED` in `src/admin/services/tenant_status_service.py` so the `/status` JSON correctly tags it as `platform` scope on embedded. This changes what Storefront (and other Tenant Management API consumers) see in their setup feed — coordinate the rollout with the upstream consumer before shipping.

### Phase 2 — Entity promotion to Configure peers

Promote the three remaining entity-shaped Settings sub-sections to standalone pages under Configure → Workspace:

| Entity | Today | After |
|--------|-------|-------|
| Publishers | `/settings#publishers` in-page section (~110 lines) | New blueprint + template at `/tenant/<id>/publishers`; Configure → Workspace entry |
| Signing Keys | `/settings#signing-keys` in-page section (~95 lines); already has deep-link affordances (`default_section == 'signing-keys'`) | New blueprint + template at `/tenant/<id>/signing-keys`; Configure → Workspace entry |
| Policies & Workflows | `/settings#business-rules` in-page section (~676 lines, complex form with multiple sub-forms: Budget Controls, Naming Conventions, Approval Workflows, Currency Limits) | New blueprint + template at `/tenant/<id>/policies`; Configure → Workspace entry. Multiple POST handlers — extract each as its own form action endpoint. |
| Integrations | `/settings#integrations` in-page section (~405 lines: Slack + Signals Agents) | New blueprint + template at `/tenant/<id>/integrations`; Configure → Workspace entry. Slack already POSTs to a dedicated `settings.update_slack` endpoint — extraction is clean. |

For each promotion:
1. Extract section markup to its own template (`templates/<entity>.html`).
2. Add Flask blueprint at `src/admin/blueprints/<entity>.py` with the GET handler and any section-specific POST handlers (most already exist).
3. Add Configure → Workspace nav entry in `templates/base.html`.
4. Delete the section from `templates/tenant_settings.html`.
5. Update tests in `tests/integration/test_embedded_ui_hardening.py` and `tests/integration/test_tenant_settings_comprehensive.py` to hit the new URL.
6. Update setup checklist `action_url` references (`_settings_url("publishers")`, `_settings_url("business-rules")`) to point at the new standalone routes.

Recommend shipping each entity promotion as its own PR to keep review focused.

### Phase 3 — Fold-in to existing primary-nav pages

- **Products section** → fold defaults config into the primary-nav `Products` page as a Settings tab.
- **Inventory section** → merge into Configure → Inventory (the four existing peers already own the entity model).

After Phase 3, Tenant Settings contains only: Account, Ad Server, Danger Zone.

### Phase 4 — Hide Tenant Settings on embedded; remove dead code

- Gate the Configure → Workspace → Tenant Settings nav entry in `templates/base.html` on `{% if not embedded_view %}`.
- Gate the `/tenant/<id>/settings` route on `not embedded_view` (return 404 or redirect to Configure → Setup Checklist).
- Delete the now-unreachable embedded branches inside `tenant_settings.html` (lines 429-438, 1120, 1939, 2206, 2234, 2842).
- Delete the dead `<div id="advertisers">` section if Buyer Agents is fully decommissioned in Phase 2; or fold its Principal-admin contents into the new Users & Access page if standalone tenants still need access-token management.

## Constraints during the rollout

1. **Don't break setup checklist links.** Every time a section moves to a new URL, the corresponding `_settings_url(...)` call in `src/services/setup_checklist_service.py` and the `_CONFIGURE_PATHS` entry in `src/admin/services/tenant_status_service.py` must update in the same PR. The structural guard at `tests/unit/test_architecture_obligation_coverage.py` doesn't catch this — coverage is purely on test assertions.

2. **Don't break in-app cross-links.** Search-and-update `url_for('tenants.tenant_settings', ...)` + `#<section>` anchors when sections promote. Current call sites (non-exhaustive): `templates/tenant_settings.html:2099` (the moved-to-Buyer-Routing banner), `src/services/setup_checklist_service.py` (action URLs), `templates/add_product.html:201` + `add_product_gam.html:273` (deep-link copy).

3. **Phase 2 + 3 are real form-extraction work.** Each in-page section has POST handlers that today share the `/tenant/<id>/settings` form-post infrastructure (CSRF, flash messages, redirect-back). Extracting cleanly means each new blueprint owns its own POST contract — verify against existing tests in `tests/integration/test_tenant_settings_comprehensive.py` and `tests/admin/test_comprehensive_pages.py` before deleting the source section.

4. **Embedded operators must never lose access mid-phase.** Phase 4 (hide Tenant Settings entirely on embedded) cannot land until Phase 2 promotions are complete and all embedded-accessible sections (Policies & Workflows, Integrations, Publishers, Signing Keys) have peer Configure entries. The fold-ins (Products, Inventory) are non-blocking — embedded tenants already access those concepts elsewhere.

## Cross-references

- [Sprint 4 (UI hardening) — Settings → Advertisers reversal](./embedded-mode-sprint-4-ui-hardening.md#settings--advertisers-reversal-sprint-7) — the Sprint 4 design decision this sprint reverses.
- [Sprint 5 (Buyer Routing UX)](./embedded-mode-sprint-5-buyer-routing-ux.md) — the half-completed promotion of advertiser mapping out of Settings, which motivated this sprint.
- `templates/tenant_settings.html` — the mega-page being decomposed.
- `templates/base.html` — the Configure menu being populated.
