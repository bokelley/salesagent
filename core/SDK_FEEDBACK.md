# SDK feedback from the salesagent greenfield-rebuild migration

Concrete friction points hit while bumping salesagent from `adcp 3.12.0` to
`main` (4.3.0+) and standing up a new `core/` agent on the framework
primitives. Sorted by impact. Each item names the pain, the time cost, and
the proposed fix.

Context: 270 files scanned, 141 codemod findings, 62 files modified to
clear the test-collection cascade, 4253 tests collecting clean.

---

## 🔴 High-impact: blocked progress until worked around

### 1. `Dimensions` / `Renders` / `Responsive` are split inconsistently

`Responsive` is on the public surface; `Dimensions` and `Renders` are not.
They sit next to each other in `adcp.types.generated_poc.core.format` and
adopters always import them as a triad.

**Pain:** my batch rewrite of `from adcp.types.generated_poc.core.format
import (...)` → `from adcp.types import (...)` worked for `Responsive` but
silently broke when the same import line included `Dimensions` or
`Renders`. Six tests had to be reverted to mixed imports.

**Ask:** add `Dimensions`, `Renders` to `adcp.types` re-exports.

### 2. `MediaBuyFeatures` and `AiTool` not on public surface

Both are referenced by adopter code (`capabilities.py`, `creative.py`,
`policy_check_service.py`) and both sit only at
`adcp.types.generated_poc.{core.media_buy_features,core.provenance}`. The
migration guide says "if a variant isn't aliased, file an issue."

**Pain:** salesagent has 5+ call sites that constructed these by name.
None of them have a stable replacement path.

**Ask:** alias both to `adcp.types`. (`AiTool` likely belongs in
`adcp.types` since it appears in 5 different bundled request models.)

### 3. `pending_activation` → `pending_start | pending_creatives` split is invisible to the codemod

`MediaBuyStatus.pending_activation` was removed in 4.x and split into
`pending_start` and `pending_creatives` based on the cause. The codemod
**does not catch this** — it surfaces as `AttributeError: type object
'MediaBuyStatus' has no attribute 'pending_activation'` at runtime, in
multiple test modules, after the import cascade is otherwise clean.

**Pain:** batch-replaced 9 files mechanically with `pending_start` to
unblock collection, then immediately added a `TODO(...)` because the
correct mapping is per-call-site (needs_creatives → pending_creatives;
schedule-future → pending_start). Semantic correctness will need a manual
pass.

**Ask:** extend the codemod to flag `MediaBuyStatus.pending_activation`
with a "use `pending_start | pending_creatives` based on cause" pointer,
or ship a deprecation alias that maps to `pending_start` and emits a
`DeprecationWarning` for one minor.

### 4. `idempotency_key` becoming required on `CreateMediaBuyRequest` is invisible to the codemod

The first runtime test failure after a clean collection was a Pydantic
`ValidationError: idempotency_key Field required`. The codemod doesn't
flag request constructions that omit now-required fields. This is one of
~600 legacy-test failures still pending.

**Ask:** codemod or runtime warning that flags request constructions
missing fields that became required. Even a `pytest`-level warning hook
would beat per-test debugging.

### 5. a2a-sdk 1.0 migration guidance is stale

`MIGRATION_v3_to_v4.md` says:
> `a2a.utils.errors.ServerError` → `a2a.types.A2AError`
> `a2a.types.DataPart` → `a2a.types.MessagePart`

In `a2a-sdk==1.0.1`:
- `A2AError` does not exist (only `A2ARequest`)
- `MessagePart` does not exist (only `Part`)
- `a2a.server.apps` (the whole submodule path) does not exist

**Pain:** `salesagent/src/a2a_server/` is hand-rolled against a2a-sdk 0.3
and won't run on 1.0+. The migration guide pointed at replacements that
also don't exist, so I gated the whole module behind an `A2A_LEGACY_AVAILABLE`
flag and skipped 6 unit-test modules. The migration target for these is
`adcp.server.serve(transport="a2a")` — but that's not what the guide
says.

**Ask:** regenerate the a2a-sdk migration section against the current
1.0.x pin, OR (better) point readers directly at `adcp.server.serve(transport="a2a")`
and acknowledge that hand-rolled a2a-sdk 0.3 servers don't have a
mechanical migration path.

---

## 🟡 Medium-impact: noticeable friction

### 6. The codemod doesn't auto-rewrite the safe `generated_poc → adcp.types` cases

83 of the 141 findings were `from adcp.types.generated_poc.X.Y import Z`
where `Z` is on the public surface today. These could be auto-rewritten
trivially: verify `hasattr(adcp.types, Z)` before substituting.

I wrote a 30-line python helper that did this for 26 of 30 files; the
remaining 4 needed manual review for symbols not yet aliased (issues #1
and #2 above). The helper logic is the kind of thing that belongs in the
codemod itself.

**Ask:** extend `python -m adcp.migrate v3-to-v4 --apply` to auto-rewrite
the `flag_private` findings whose target symbol is verifiable on the
public surface, leaving the unsafe ones flagged.

### 7. Numbered Assets renaming is documented but not migrated

The migration guide table is good (`Assets5 → VideoFormatAsset`, etc.)
but it's a manual lookup. Same fix as above: the codemod knows the
mapping, it can rewrite mechanically.

**Ask:** auto-apply the numbered-Assets table in
`--apply` mode. Currently it's `flag_numbered` only.

### 8. `validate_platform` lives in `adcp.decisioning.dispatch`, not `validate_capabilities`

I wasted a couple of minutes guessing at `adcp.decisioning.validate_capabilities.validate_platform` (which exists as a module but exports `validate_capabilities_response_shape`, `validate_response`, **not**
`validate_platform`). The function is in `adcp.decisioning.dispatch`.

**Ask:** re-export from `adcp.decisioning.validate_capabilities` (or even
better, `adcp.decisioning` top-level). The `validate_capabilities` module
name reads like the right home.

### 9. Soft-warned-required methods contradict the canonical example

`hello_seller.py` implements 5 methods. `validate_platform` warns about 4
additional missing methods (`get_media_buys`, `list_creative_formats`,
`list_creatives`, `provide_performance_feedback`) that are "required by
the SalesPlatform Protocol for any sales-* specialism in v6.0 rc.1+."

So the canonical minimal example trips the soft-warn. Either the example
should implement them, or the warning's threshold is wrong.

**Ask:** decide which it is and align. If the 4 are genuinely required,
update `hello_seller.py` to demonstrate the full minimum surface.

### 10. `RequestContext` is hard to construct in tests

The dataclass has 11 fields, 4 with `<factory>` defaults (`metadata`,
`account`, `now`, `state`, `resolve`). For unit testing through the
PlatformHandler, the only thing the handler reads is `ctx.account` and
sometimes `ctx.request_id`. Constructing a `RequestContext` for tests
requires guessing which factory defaults are safe.

I hit `TypeError: unexpected keyword argument 'adopter_request'` when I
read a docstring that mentioned the field but it's not a constructor
parameter. The test-friendly path took several tries.

**Ask:** ship `adcp.testing.make_request_context(account=..., **overrides)`
with sane defaults and a stable signature documented as the test seam.

### 11. `create_adcp_server_from_platform` doesn't accept `name=`, but `serve()` does

Building the ASGI app in tests requires going through `create_adcp_server_from_platform()
→ create_mcp_server()` and the kwarg surface differs between the two. The
docs in `serve.py` are great but the API ergonomics for "I want to test
this without a network port" force adopters into a less-documented path.

**Ask:** first-class `adcp.testing.build_asgi_app(platform, name=...) →
ASGIApp` helper that's officially the "for tests" way to get a running
server. The current docstring on `create_mcp_server` is a great
implementation reference but it's not framed as the test seam.

---

## 🟢 Low-impact: nice to have

### 12. Two proposal surfaces (`adcp.server.proposal` + `adcp.decisioning.proposal_manager`)

`adcp.server.proposal` exports `AllocationBuilder`, `proposals_not_supported`.
`adcp.decisioning.ProposalManager` is the new (PR #504) async-managed surface.

Adopters have to know which to use when. The names overlap.

**Ask:** docs section in `docs/proposals/` (if not already there) that
maps "I want to..." → "use this surface."

### 13. `SubdomainTenantMiddleware` requires double host registration

Registering `acme.localhost` once in `InMemorySubdomainTenantRouter`
covers both `acme.localhost` and `acme.localhost:3001` at lookup time
(per `_normalize_host`), but the FastMCP `allowed_hosts` allowlist
needs both `acme.localhost` and `acme.localhost:*` registered explicitly
to cover the same surface. I worked around it in `core/main.py`'s
`_allowed_hosts()` helper.

**Ask:** make `SubdomainTenantMiddleware` (or `serve()`) accept a single
host list and synthesize the `:*` variants for the allowlist
automatically. The existing port-stripping at lookup time is great; the
allowlist surface should be symmetric.

### 14. `PlatformHandler.advertised_tools` lists every spec tool by default

A fresh `MockSellerPlatform` (5 methods) shows ~50 entries in
`handler.advertised_tools` after `create_adcp_server_from_platform()`.
The `advertise_all` flag controls this but isn't surfaced in
`create_adcp_server_from_platform`'s signature — only in
`serve()`/`create_mcp_server`. So the standalone "build the handler"
path doesn't have an obvious knob for "advertise only what I implement."

**Ask:** add `advertise_all=False` (default) to
`create_adcp_server_from_platform`'s signature and have it filter
`advertised_tools` to declared methods, matching `serve()`'s default.

### 15. Migration guide test count was 161; current state was 141

The migration guide says salesagent's prior v3→v4 attempt hit "270 files
scanned, 161 test-collection failures." The current scan hit 141
findings (different denominator — findings vs failures) and 36 collection
errors. Numbers don't directly compare but the framing made me expect
worse pain than I actually hit. The 4.x improvements between the prior
attempt and now (more aliases, better codemod) are working.

**Ask:** consider re-running the codemod against a current snapshot of
adopter codebases for the migration guide's "what to expect" numbers, OR
note in the guide that the figures reflect the worst case at v4.0
release.

---

## 🎯 Aggregate ask: consider a `--auto-apply` mode

Of the 141 findings:
- **83 `flag_private`** with public-surface aliases → auto-rewritable
- **27 `flag_numbered` Assets** with documented semantic aliases → auto-rewritable
- **31 `flag_removed`** (Pricing, BrandManifest, etc.) → genuinely needs human review

So **78% of findings are mechanically auto-rewritable** with a verifier
that checks the public surface. Today the codemod refuses to rewrite any
of them in `--apply` mode. A `--auto-apply` mode that rewrites the safe
78% and flags the rest would have shaved most of an afternoon off this
migration.

The 30-line helper I wrote in salesagent does roughly this for
`flag_private` (see `core/SDK_FEEDBACK.md` git history).

---

## What worked extremely well — keep doing this

- **`PlatformRouter` for multi-tenant.** Drop-in replacement for the
  hand-rolled `ADAPTER_REGISTRY` pattern. The docstring explicitly says
  "the migration target for adopters with a salesagent-shaped pattern"
  and that's exactly what it is.
- **`SubdomainTenantMiddleware`.** Wholesale replacement for nginx
  Host-header routing. ~250 LOC of salesagent's `domain_routing.py`
  becomes a 5-line wiring call.
- **`AccountStore` Protocol with three reference impls.** The
  `Singleton/Explicit/FromAuth` choice covers most adopter shapes; for
  salesagent's tenant-prefixed account-id convention, copying the
  `MultiTenantAccountStore` example required ~30 LOC.
- **Codemod's structured JSON output.** Even for the 22% it can't
  auto-fix, the JSON is exactly the right shape to drive a follow-up
  helper script. Saved a lot of grep cycles.
- **`hello_seller.py` and `multi_platform_seller/`.** Both examples are
  exactly what an adopter needs to crib from. The `multi_platform_seller`
  one in particular nailed the docstring-as-architecture-explanation
  shape.
- **The migration guide's "salesagent v3→v4 experiment, 161 failures"
  context.** It's rare and excellent to see a migration guide
  acknowledge specific real-world adopter pain. The `_base.py` cascade
  warning was the difference between a 4-hour migration and a 20-minute
  one.
- **CHANGELOG specificity.** "expand with salesagent migration
  production patterns (#326)" is the kind of changelog line that lets
  adopters know exactly what's relevant to them. Keep this style.
