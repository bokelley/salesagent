# Sprint 4 Spec: UI Hardening for Embedded-Mode Tenants

**Parent design:** [embedded-mode](./embedded-mode.md)
**Builds on:** [sprint 1.7](./replace-authorized-properties-with-aao-lookup.md), [sprint 1.8](./embedded-mode-sprint-1.8-buyer-advertiser-routing.md)
**Status:** Captured (sprint-4 territory)
**Last updated:** 2026-05-04

## Scope

When `tenant.is_embedded=true`, the publisher-facing admin UI
hides platform-managed surfaces. We pre-fill these at provision time
or own them via the upstream platform — surfacing the "edit" affordance
in the publisher iframe would invite drift between the platform's
record and the salesagent's.

This spec is the publisher-iframe counterpart to Sprint 1.8 §6
(model-layer guard + Pydantic validators). §6 hardens the *write* path;
this sprint hides the *read*-only-when-managed surfaces from the UI
entirely so publishers don't see fields they can't edit.

## Hidden surfaces

### Hidden pages (entire route)

| Route | Why |
|---|---|
| `/tenant/{id}/users` | Identity flows through `X-Identity-*` per the [identity contract](../integration/embedded-mode-identity-contract.md) — no salesagent-side User records needed for embedded tenants |

### Hidden sections within `/tenant/{id}/settings`

#### Account
- **Organization Information** (tenant `name`, `billing_plan`) — host product owns
- **Branding** (operator_domain / brand resolution) — upstream-owned
- **Domain Configuration** (custom CNAME) — unused in embedded mode
- **🔐 Access Control** (SSO/OIDC) — identity forwarded via X-Identity-* contract

#### Ad Server Configuration
- **Available Ad Servers** (selector) — locked at provision
- **Google Ad Manager Configuration** (creds) — owned via Tenant Mgmt API

### Currency: lock to single, read-only

- Currency is set at provision time and matches what the upstream platform
  configured (upstream owns billing-currency identity).
- Hide the multi-currency add/remove UI on embedded tenants.
- Render read-only in Organization Information (or wherever currency
  currently surfaces) so the publisher can see it but not change it.

## Kept visible (publisher-managed, per sprint-1 design)

These remain editable for embedded tenants — they're publisher business logic,
not platform infrastructure:

- **Policies & Workflows**
  - Budget, Brand Manifest, Naming, Measurement, Approval, Creative
    Review, Policy, Product Ranking
- **Inventory Management**
  - Sync Status — render read-only; the manual sync trigger is
    consolidated into the upstream platform's "Refresh tenant" action
    (sprint 1.8 §8).
- **Publisher Partnerships**
- **Product Management**
- **Advertiser Management**
- **Integrations** (Slack, AI Services, Creative Agents)

## Banner pattern on hidden URLs

Hidden-section URLs render the standard banner instead of returning 404:

> **Platform settings managed by {host product name}.**  *(banner reads `tenant.external_source` through a display-name filter)*

This is a deep-link safety net for the setup-tasks panel (sprint 1.8 §7):
when a publisher clicks a `configure_path` from a status item that's
`scope=platform`, they should land on a "this is owned by your platform"
explanation rather than a dead end.

The banner template should:
- Reuse the existing `templates/_embedded_mode_banner.html` partial if one
  exists (sprint 1.8 §6 first half ships a similar lock banner on the AAO
  inputs); otherwise add a shared partial with the same chrome.
- Include the upstream platform's display name dynamically:
  `{{ tenant.external_source | default('your platform') | title }}`.
- Optionally link out to the upstream platform's settings page when known
  (`tenant.external_admin_url` if we ever surface one — currently NULL).

## Implementation notes

### Hiding pages
Pattern in `src/admin/blueprints/users.py` (illustrative):

```python
@bp.route("/tenant/<tenant_id>/users")
def users_index(tenant_id):
    tenant = get_tenant_or_404(tenant_id)
    if tenant.is_embedded:
        return render_template("_embedded_mode_locked.html", tenant=tenant), 200
    # ... existing code
```

`200` (not 404) because the route still exists logically — the publisher
just can't manage users from this side.

### Hiding sections
`templates/tenant_settings.html` gains `{% if not tenant.is_embedded %}`
guards around the hidden sections. The Settings nav (left rail) drops the
matching tabs entirely so there's no "Access Control (locked)" stub.

### Currency lock
`templates/tenant_settings.html` Organization Information block:

```jinja
{% if tenant.is_embedded %}
  <div class="form-group">
    <label>Currency</label>
    <input type="text" value="{{ tenant.default_currency }}" readonly>
    <small class="text-muted">Set by your platform at provisioning.</small>
  </div>
{% else %}
  <!-- existing multi-currency add/remove UI -->
{% endif %}
```

The model-layer guard already blocks `CurrencyLimit` writes from non-API
callers on embedded tenants (sprint 1.8 §6 first half — `CurrencyLimit`
would need to join the protected set if it isn't already). UI hide is
defense-in-depth.

## Acceptance criteria

### Hidden pages
- [ ] `/tenant/{id}/users` on an embedded tenant returns 200 with the
      "Platform settings managed by {host product name}." banner — not 404.
- [ ] Same route on an open-instance tenant continues to render the
      Users & Access page unchanged.

### Hidden settings sections
- [ ] Settings page on an embedded tenant omits the Account → Organization
      Information, Branding, Domain Configuration, Access Control sections.
- [ ] Same page omits Ad Server Configuration entirely.
- [ ] Settings nav (left rail) drops the matching tabs.
- [ ] All hidden sections render the banner if accessed via direct URL
      anchor (e.g. `/tenant/{id}/settings#access-control`).

### Currency lock
- [ ] Managed tenant Settings shows currency as read-only in Org Info.
- [ ] Multi-currency add/remove UI is hidden.
- [ ] Open-instance tenant continues to see the editable multi-currency
      panel (no behavior change).

### Kept-visible surfaces (regression guards)
- [ ] Policies & Workflows tab fully editable on embedded tenants.
- [ ] Publisher Partnerships, Products, Advertisers, Integrations all
      editable on embedded tenants.
- [ ] Inventory Sync Status is read-only on embedded tenants but visible.

## Cross-references

- Sprint 1.6 §6 first half — Pydantic validators + model-layer guard
  (write-path lockdown). This sprint is the read-path counterpart.
- [Identity contract](../integration/embedded-mode-identity-contract.md)
  — drives the "no Users page" decision.
- Sprint 1.8 §7 — `setup_tasks` block. `configure_path` items with
  `scope=platform` land on the banner page rather than dead-ends.
- Sprint 1.8 §8 — "Refresh tenant" button replacement for per-sync triggers.

## Sprint placement

**Sprint 4** — slots after the model-layer/lockdown work in 1.8 ships
end-to-end. UI hardening is mostly cosmetic (the model-layer guard is
the actual safety) so it doesn't gate commercial go-live, but it's the
last polish required before Storefront publishers see the iframe in
production.

Estimated scope: **~1.5–2 days** (mostly template changes + one shared
banner partial + tests for the hide/show matrix).
