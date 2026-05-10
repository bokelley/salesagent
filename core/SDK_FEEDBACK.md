# SDK feedback — open items

Tracker for adopter friction with `adcp-client-python` (currently pinned to
**v4.6.1**, declares spec version **3.0.7**). The original three rounds of
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
gets wired through `serve()`'s `allowed_hosts=` parameter. The actual fix
probably lives in `modelcontextprotocol/python-sdk`.

**Local tracker:** salesagent #26.

#### 2. SpecDefaults: pre-validation request hook / spec-default registry

Some 4.4+ schemas mark fields as `required` at the wire level even though
the spec instructs sellers to apply a default for missing values from
pre-v3 clients (`GetProductsRequest.buying_mode → 'brief'`, `account` and
`idempotency_key` on tools that resolve identity from the auth chain,
`format_id` shape, asset-type backfill, etc.). The typed dispatcher
validates payloads BEFORE the handler runs, so a per-handler
`model_validator` is too late.

**Workaround:** [core/middleware/spec_defaults.py](core/middleware/spec_defaults.py)
(273 LOC) — bytes-rewrites JSON-RPC bodies before serve() sees them.

**Better SDK shape:** expose a pre-validation request hook (per-tool or
global) that adopters can register defaults against, OR a declarative
`spec_default_registry={"get_products": {"buying_mode": "brief"}}` kwarg
on `serve()`. Either kills the entire 273-LOC middleware.

**Upstream:** [#614](https://github.com/adcontextprotocol/adcp-client-python/issues/614).

#### 3. Nested `model_dump()` resolution in response models

Pydantic doesn't auto-call custom `model_dump()` on nested models. Every
response model with structured children needs a manual override that
walks the children. We have ~59 such overrides in
[src/core/schemas/](src/core/schemas/).

**Better SDK shape:** SDK base model that recursively dispatches custom
`model_dump()` on child models, OR codegen flag that emits nested-aware
serialization on the generated types. Highest-LOC win on the list.

**Upstream:** [#615](https://github.com/adcontextprotocol/adcp-client-python/issues/615).

#### 4. Agent-card public URL injection (`X-Forwarded-Host`-aware)

`adcp.server.a2a_server._build_agent_card` hardcodes
`http://localhost:{port}/` at server-init time. SDK clients reading
`/.well-known/agent-card.json` from production then try to reach the
internal socket and every A2A request fails with `fetch failed`.

**Workaround:** [core/middleware/agent_card_public_url.py](core/middleware/agent_card_public_url.py)
(190 LOC) — buffers the response and rewrites localhost URLs based on
`X-Forwarded-Host` / `Host`.

**Better SDK shape:** `serve(public_url=...)` kwarg, OR honour
`X-Forwarded-Host` / `Host` natively when building the card.

**Upstream:** [#616](https://github.com/adcontextprotocol/adcp-client-python/issues/616).
**Local tracker:** salesagent #103.

#### 5. Expose `RequestContext.transport` (mcp | a2a)

`RequestContext` doesn't carry the inbound transport identifier. Webhook
payload shape selection is transport-dependent (A2A buyers receive
`Task`/`TaskStatusUpdateEvent`; MCP buyers receive `McpWebhookPayload`),
so platform methods need this signal.

**Workaround:** [core/middleware/transport_detect.py](core/middleware/transport_detect.py)
(85 LOC) — sets a `current_transport` ContextVar from URL path.

**Better SDK shape:** `RequestContext.transport: Literal["mcp", "a2a"]`
populated by the framework's existing dispatch.

**Upstream:** [#617](https://github.com/adcontextprotocol/adcp-client-python/issues/617).

#### 6. Public test-harness app builder

In-process test harnesses need the ASGI app without binding a uvicorn
socket. We import private symbols today: `from adcp.server.serve import
_apply_asgi_middleware, _build_mcp_and_a2a_app` and
`from adcp.decisioning.serve import create_adcp_server_from_platform`.

**Workaround:** [core/main.py:build_app](core/main.py) — calls private
helpers to build the unified app for `httpx.ASGITransport` tests.

**Better SDK shape:** `adcp.testing.build_asgi_app(platform, **opts)`
that returns the ASGI handler ready for `httpx.ASGITransport`. Mirrors
the JS SDK's testing primitives. (Related: closed #549/PR #554 shipped
`build_test_client` — this would extend the same pattern to ASGI app
construction.)

**Upstream:** [#618](https://github.com/adcontextprotocol/adcp-client-python/issues/618).

#### 7. TenantRegistry parity with JS `createTenantRegistry`

Python ships `CallableSubdomainTenantRouter` (host-routing callback) and
`LazyPlatformRouter` (per-tenant platform factory). JS ships
`createTenantRegistry` which adds: per-tenant health states (pending /
healthy / unverified / disabled), runtime `register(tenantId, config)` /
`unregister` without restart, `recheck(tenantId)` for key rotation, and
`awaitFirstValidation` boot semantics.

**Workaround:** custom tenant lifecycle in
[core/main.py](core/main.py) + admin-flow `invalidate(host)` calls on
tenant create / deactivate / subdomain rotate.

**Better SDK shape:** Python parity for `createTenantRegistry`. Composes
with `BearerTokenAuth(validate_token=...)` for principal-token auth (no
JWKS dependency required).

**Upstream:** [#619](https://github.com/adcontextprotocol/adcp-client-python/issues/619).

### Helper-typing gaps (surfaced during cleanup)

- **`create_mcp_webhook_payload`** should accept any `BaseModel` for
  `result` (it handles `model_dump` internally) and return
  `McpWebhookPayload`, not `dict[str, Any]`. Three call sites currently
  cast or `.model_construct()` around the type.
  **Upstream:** [#607](https://github.com/adcontextprotocol/adcp-client-python/issues/607) (open, claude-triaged).

### Strictness ergonomics

- **Schema inheritance × strict mypy** — extending library types and
  overriding nested fields with a more-specific element type triggers
  `[assignment]` errors under `mypy --strict`. ~50 `# type: ignore[assignment]`
  comments in [src/core/schemas/](src/core/schemas/) for this single pattern.
  **Upstream:** [#624](https://github.com/adcontextprotocol/adcp-client-python/issues/624).
- **Adopter type-checking test suite** — SDK runs `mypy --strict` on its
  own code but doesn't verify adopter extension patterns are strict-clean.
  Proposes a `tests/type_checks/` mirror of the JS SDK's `*.type-checks.ts`
  files: each adopter pattern is a small file that must pass `mypy --strict`
  with zero `type: ignore`. CI gate ensures regressions surface in SDK PRs
  rather than downstream upgrades.
  **Upstream:** [#625](https://github.com/adcontextprotocol/adcp-client-python/issues/625).

### Stretch / nice-to-have

- **`adcp.upstream.gam` helper** — service-account auth + cached client
  (~30 LOC, identical across any salesagent-shaped GAM adopter).
- **`placement_to_product` projection helper** — mechanical fields
  (format_ids from sizes, default pricing_options, etc.) are identical
  across publisher-config-vs-product mapping.

## Closed since prior rounds

Major upstream fixes (all merged):
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
