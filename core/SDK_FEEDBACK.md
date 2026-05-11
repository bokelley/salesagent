# SDK feedback — open items

Tracker for adopter friction with `adcp-client-python` (currently pinned to
**v5.1.0**, declares spec version **3.0.7**). The original three rounds of
feedback (most items now merged upstream) live in this file's git history.

## Currently open

### Framework gaps

#### 1. MCP DNS-rebinding allowlist needs subdomain wildcards or a callable

`mcp.server.transport_security._validate_host` only matches exact hosts and
`host:*` port wildcards — NOT subdomain wildcards like `*.localhost` or
`*.localtest.me`. Multi-tenant deployments where every tenant is a
subdomain must either enumerate every active tenant in the allowlist on
every boot OR disable DNS-rebinding protection entirely.

**Workaround:** enumerate dev tenant subdomains at
[core/main.py:_allowed_hosts](core/main.py).

**Better SDK shape:** the allowlist should accept either glob-style
subdomain wildcards OR a callable `validate_host(host: str) -> bool` that
gets wired through `serve()`'s `allowed_hosts=` parameter.

**Upstream:** [`modelcontextprotocol/python-sdk#2141`](https://github.com/modelcontextprotocol/python-sdk/issues/2141) (open — fix lives in mcp/python-sdk, not adcp-client-python).
**Local tracker:** salesagent #26.

#### 2. Agent-card public URL — per-request `X-Forwarded-Host` rewrite

adcp 5.0 added a static `serve(public_url=...)` kwarg (#621), which we now
wire from the `PUBLIC_URL` env. That covers single-host deployments. For
multi-tenant subdomain deployments where each tenant has its own public
host, the static kwarg can only advertise one — our middleware still
rewrites per-request from `X-Forwarded-Host` to surface the buyer's
actual tenant host.

**Workaround:** [core/middleware/agent_card_public_url.py](core/middleware/agent_card_public_url.py)
(190 LOC) — buffers the response and rewrites localhost URLs based on
`X-Forwarded-Host` / `Host`. Composes cleanly with the new static kwarg
(only rewrites loopback URLs, so it no-ops when `public_url` is set to a
non-loopback value).

**Better SDK shape:** make the agent-card URL `X-Forwarded-Host`-aware
when no static `public_url` is configured, OR accept a
`Callable[[Request], str]` for per-request resolution.

**Upstream:** [#647](https://github.com/adcontextprotocol/adcp-client-python/issues/647) (open — follow-up to closed #616 which shipped Option A static kwarg only; Option B per-request was explicitly deferred).
**Local tracker:** salesagent #103.

### Strictness ergonomics

- **Schema inheritance × strict mypy** — extending library types and
  overriding nested fields with a more-specific element type triggers
  `[assignment]` errors. After PR #640 propagated, our remaining
  `# type: ignore[assignment]` lines (~12 in [src/core/schemas/](src/core/schemas/))
  are all **genuine cross-class cases** that #640 can't reach — e.g.
  `MediaBuyDeliveryData` extends our `SalesAgentBaseModel` while the
  parent expects library `MediaBuyDelivery`; `geo_*_exclude` uses
  `GeoCountry` while the parent expects `GeoCountriesExcludeItem`
  (shape-identical, distinct classes per #642 analysis).
  **Upstream:** [#624](https://github.com/adcontextprotocol/adcp-client-python/issues/624) (closed; covers Sequence[X]).
  **Spec rename:** [adcontextprotocol/adcp#4347](https://github.com/adcontextprotocol/adcp/issues/4347) — the structural fix for the cross-class cases.
- **Adopter type-checking test suite** — Closed by [#625](https://github.com/adcontextprotocol/adcp-client-python/issues/625) (5.0).

### Stretch / nice-to-have

- **`adcp.upstream.gam` helper** — service-account auth + cached client
  (~30 LOC, identical across any salesagent-shaped GAM adopter).
- **`placement_to_product` projection helper** — mechanical fields
  (format_ids from sizes, default pricing_options, etc.) are identical
  across publisher-config-vs-product mapping.

## Closed since prior rounds

### Shipped in adcp 5.1.0

- [#648] `adcp.server.spec_compat_hooks()` — canonical pre-v3 / pre-4.4 buyer compat hook registry. Replaces our 150-LOC local `core/spec_default_hooks.py` (deleted).
- [#649] `TenantRegistry.as_platform()` — bridges the registry into a `DecisioningPlatform` for `serve()`. (Local adoption tracked separately.)
- [#655] `build_asgi_app(pre_validation_hooks=...)` — `adcp.testing.build_asgi_app` now forwards pre-validation hooks, so in-process test apps exercise the same compat layer as production.
- [#656] `pydantic.ValidationError → INVALID_REQUEST` translation built into `serve()` (salesagent #330 stops being adopter-side).

### Shipped in adcp 5.0.0

- [#614 / PR #629] `serve(pre_validation_hooks=...)` — replaces our 273-LOC `SpecDefaultsMiddleware` with pure-Python hooks.
- [#615 / PR #639] Nested `model_dump()` — `AdCPBaseModel.model_dump()` defaults `serialize_as_any=True`.
- [#616 / PR #621] `serve(public_url=...)` (static kwarg only — per-request case still open as #2 above).
- [#617 / PR #627] `RequestContext.transport` + `adcp.server.current_transport` ContextVar — kills our `TransportDetectMiddleware` (85 LOC).
- [#618 / PR #626] Public `adcp.testing.build_asgi_app` — replaces our 2 private-API imports in `core/main.py:build_app`.
- [#619 / PR #628] `TenantRegistry` with `register_lazy`.
- [#624 / PR #640] `Sequence[X]` widening on extension-point list fields.
- [#625 / PR #634] Adopter type-checking test suite with zero-ignore contract.
- [#643] `canceled: Literal[True] | None = None` codegen — our local overrides are now redundant.
- [#607 / PR #632] `create_mcp_webhook_payload` returns `McpWebhookPayload` directly.
- [#613] `/.well-known/agent.json` alias route registered upstream — kills our `WellKnownAgentJsonRedirectMiddleware`.

### Shipped in adcp 4.x

- #544 `CallableSubdomainTenantRouter`
- #545 `BearerTokenAuthMiddleware` `header_name` + `bearer_prefix_required`
- #555 `IdempotencyStore.PgBackend`
- #560 `inject_context` on `AdcpError` raise path
- #566 `serve(auth=BearerTokenAuth(...))` wires both MCP + A2A
- #567 `@IdempotencyStore.wrap` × arg-projected methods
- #570/#575 `'submitted'` wire status oneOf resolution
- #571/#574 `ctx.caller_identity` composite scope-key (docs + bearer auth)
- #598/#600 `extract_webhook_result_data` typing
- #602 webhook `to_wire_dict()` serialization seam
- `validate_idempotency_wiring` × `LazyPlatformRouter`:
  `_adcp_idempotency_external = True` is now a documented public escape
  hatch in `adcp.decisioning.validate_idempotency` (4.5.0+).

Plus a long list of public-surface aliasing and codemod improvements
already shipped on `main`.

The full historical record lives in this file's git history.
