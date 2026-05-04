# core/ — greenfield rebuild on adcp 4.3

Status: **skeleton**. Live wiring lands after the v3→v4 migration on `src/` (141 findings, see codemod report).

## Why this exists

Replace the hand-rolled MCP/A2A/nginx/wrapper machinery in `src/` with the
framework primitives shipped in [`adcp>=4.3`](https://github.com/adcontextprotocol/adcp-client-python):

| salesagent today (`src/`)                            | greenfield (`core/`)                              |
|------------------------------------------------------|---------------------------------------------------|
| `src/core/main.py` MCP wrappers + `_impl()` shims    | subclass `adcp.decisioning.DecisioningPlatform`   |
| `src/core/tools.py` A2A `*_raw` functions            | same handler, served by `serve(transport="a2a")`  |
| `src/core/domain_routing.py` (~250 LOC nginx routing)| `adcp.server.SubdomainTenantMiddleware`           |
| `src/adapters/__init__.py` `ADAPTER_REGISTRY` dict   | `adcp.decisioning.PlatformRouter`                 |
| `src/core/auth.py` + `resolved_identity.py`          | `adcp.decisioning.AccountStore` Protocol          |
| Custom webhook delivery + retry                      | `adcp.webhook_supervisor_pg.PgWebhookDeliverySupervisor` |
| `tests/bdd/`, structural guards for transport boundary | `adcp.server.TestControllerStore` + storyboards |

The framework's typed handler signatures *are* the transport boundary — no
guard tests required. The `tenant_router.py` docstring upstream literally
says it's the migration target for salesagent's `domain_routing.py`.

## ORM is shared with `src/`

`core/stores/*.py` import the existing SQLAlchemy models from
`src.core.database.models`. Both stacks read/write the same Postgres tables
through one source of truth. Migrations stay in `alembic/`. As port-back
progresses, `src/` shrinks; the ORM and migrations don't move.

## Layout

```
core/
├── main.py              # PlatformRouter + serve() entrypoint
├── tenancy.py           # SubdomainTenantRouter backed by Tenant ORM
├── auth.py              # AccountStore impl over Principal/Tenant
├── platforms/
│   ├── mock.py          # DecisioningPlatform subclass (first milestone)
│   ├── gam.py           # ported later
│   └── kevel.py         # ported later
├── stores/
│   ├── accounts.py      # AccountStore impl
│   ├── media_buy.py     # MediaBuyStore (targeting_overlay echo)
│   ├── tenants.py       # tenant lookup for the router
│   └── audit.py         # AuditSink over audit_logs table
├── management_api.py    # FastAPI: tenant/principal/token CRUD (replaces /admin)
└── tests/
    └── storyboards/     # media_buy_seller storyboard + pytest harness
```

## Milestones

- **M1 — first runnable.** `MockSellerPlatform` answers `get_products` end-to-end.
  Subdomain routing works. `media_buy_seller` storyboard reaches at least the
  `get_products` step against the existing `tenants`/`products` rows.
- **M2 — full mock seller.** Pass the entire `media_buy_seller` storyboard
  (9 steps) including `create_media_buy`, `sync_creatives`, delivery polling.
- **M3 — port GAM adapter.** Subclass `DecisioningPlatform` with the
  existing GAM client + reporting code. Run side-by-side with `src/` against
  the same DB.
- **M4 — port admin operations.** Replace `src/admin` Flask UI with the
  management API (humans hit FastAPI directly; no Google OAuth in v1, just
  signed tokens).
- **M5 — retire `src/`.** Once every adapter and auth flow runs through
  `core/`, delete the legacy tree.

## What's deliberately deferred

- ProposalManager (in flight in `src/`); the framework's `proposal.py` ships
  builders — port the in-flight work to land upstream rather than duplicating
- A2A transport (one-line flip via `serve(transport="a2a")` once MCP is solid)
- Workflow engine (revisit whether the framework's task registry is enough)
- Approximated DNS automation (not needed if we ditch nginx and use a single
  wildcard cert behind a load balancer)

## How to follow along

The codemod report at `/tmp/codemod-report.json` lists every v3→v4 issue in
`src/`. Until those are fixed and `pyproject.toml` is bumped to `adcp>=4.3`,
nothing in `core/` actually runs.
