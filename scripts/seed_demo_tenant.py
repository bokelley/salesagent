"""Demo-tenant seed script for the Buyer Routing UI / Storefront iframe demo.

Spins up an embedded-mode tenant pre-populated with the kinds of data a
publisher would have after a few days of real traffic, so an engineer can
navigate the Buyer Routing UI / Storefront integration and see the full
picture without hand-clicking everything.

================================================================================
RUNBOOK
================================================================================

Prerequisites:
- A reachable Postgres instance and ``DATABASE_URL`` exported. The fastest
  path on a worktree agent is the agent-db skill::

      eval $(.claude/skills/agent-db/agent-db.sh up)

  or load the env directly::

      source .claude/skills/.agent-db.env

  Production-shaped runs against the docker-compose stack work too — set
  ``DATABASE_URL=postgresql://...`` to whatever Postgres the admin UI talks to.

- Tables must exist (the script does NOT run migrations; it assumes the schema
  is current). Run ``uv run python scripts/ops/migrate.py`` first if needed.

Run from the host (no Docker exec required):

    uv run python scripts/seed_demo_tenant.py             # https URLs
    uv run python scripts/seed_demo_tenant.py --localhost # http://localhost storefront
    uv run python scripts/seed_demo_tenant.py --tenant-id demo_tid_001  # idempotent re-seed
    uv run python scripts/seed_demo_tenant.py --days 14   # backfill 14d of buyer activity
    uv run python scripts/seed_demo_tenant.py --clean     # remove demo rows then exit

Optional flags:
    --tenant-id ID         # reuse / update an existing demo tenant id (idempotent)
    --host-source NAME     # external_source value (default "demo-host")
    --localhost            # rewrite https URLs to http://localhost variants
    --days N               # backfill window for MediaBuy timestamps (default 30)
    --clean                # delete every tenant whose external_source matches the
                           #   --host-source value, then exit. Refuses if the
                           #   external_source has any non-demo data.

Exit codes:
    0  success — tenant created (or refreshed, or cleaned)
    1  failure — bad input, validation error, missing schema row, etc.

This script connects directly to the database via ``get_db_session()`` and
sets ``session.info["management_api_caller"] = True`` to bypass the
embedded_tenant_guard — the seed impersonates the Tenant Management API.
"""

from __future__ import annotations

import argparse
import os
import random
import string
import sys
import uuid
from datetime import UTC, datetime, timedelta

# Ensure src/ is importable when run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from src.core.database.database_session import get_db_session  # noqa: E402
from src.core.database.models import (  # noqa: E402
    Account,
    AdapterConfig,
    AdvertiserRoutingRule,
    CurrencyLimit,
    GamAdvertiser,
    MediaBuy,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)

# Tables we delete from in the script's clean / re-seed paths, in FK order.
# Used when ORM cascade would either fail (incomplete cascade declarations on
# gam_advertisers / advertiser_routing_rules) or load JSONType-bound columns
# whose stored values predate a schema tightening.
_CHILD_TABLES_FK_ORDER: tuple[str, ...] = (
    "media_buys",
    "accounts",
    "products",
    "principals",
    "gam_advertisers",
    "advertiser_routing_rules",
    "currency_limits",
    "property_tags",
    "adapter_config",
)


def _delete_tenant_rows(session, tenant_ids: list[str]) -> None:
    """Hard-delete a tenant and all child rows in FK-safe order via raw SQL.

    ORM cascade is intentionally bypassed for two reasons:
      1. Some child relationships (gam_advertisers, advertiser_routing_rules)
         don't declare ``cascade="all, delete-orphan"`` on the Tenant side, so
         ``session.delete(tenant)`` raises an AssertionError trying to null a PK.
      2. Loading Account rows triggers JSONType validation on
         ``Account.brand``; pre-existing demo data with hyphens (now disallowed
         by the BrandReference regex) would prevent reloading the parent.
    Raw DELETE bypasses both pitfalls.
    """
    if not tenant_ids:
        return
    for tbl in _CHILD_TABLES_FK_ORDER:
        session.execute(
            text(f"DELETE FROM {tbl} WHERE tenant_id = ANY(:ids)"),
            {"ids": tenant_ids},
        )
    session.execute(
        text("DELETE FROM tenants WHERE tenant_id = ANY(:ids)"),
        {"ids": tenant_ids},
    )


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------


DEFAULT_ADVERTISER_ID = "default_adv_demo"

GAM_ADVERTISERS: list[dict[str, str]] = [
    {"advertiser_id": "default_adv_demo", "name": "Acme Publisher Default", "status": "active"},
    {"advertiser_id": "wpp_coke", "name": "WPP - Coca-Cola", "status": "active"},
    {"advertiser_id": "wpp_pepsi", "name": "WPP - Pepsi", "status": "active"},
    {"advertiser_id": "publicis_general", "name": "Publicis - General", "status": "active"},
    {"advertiser_id": "legacy_archived", "name": "Legacy Advertiser (archived)", "status": "inactive"},
]


ROUTING_RULES: list[dict[str, str | None]] = [
    {
        "operator_domain": "wpp.com",
        "brand_house": "cocacola.com",
        "brand_id": None,
        "gam_advertiser_id": "wpp_coke",
    },
    {
        "operator_domain": "wpp.com",
        "brand_house": "pepsi.com",
        "brand_id": None,
        "gam_advertiser_id": "wpp_pepsi",
    },
    {
        "operator_domain": "publicis.com",
        "brand_house": None,
        "brand_id": None,
        "gam_advertiser_id": "publicis_general",
    },
]


# (account_label, operator, brand_house, brand_id, resolved_via, gam_advertiser_id, num_media_buys)
ACCOUNT_SEEDS: list[tuple[str, str, str, str | None, str, str, int]] = [
    ("WPP / Coca-Cola / Sprite", "wpp.com", "cocacola.com", "sprite", "exact", "wpp_coke", 22),
    ("WPP / Pepsi / Mountain Dew", "wpp.com", "pepsi.com", "mountain_dew", "exact", "wpp_pepsi", 17),
    ("WPP / Coca-Cola (house)", "wpp.com", "cocacola.com", None, "house", "wpp_coke", 9),
    ("Publicis / Long-tail", "publicis.com", "random-brand.example", None, "operator", "publicis_general", 14),
    ("Unknown Buyer A", "unknown-buyer-a.example", "small-brand.example", None, "default", DEFAULT_ADVERTISER_ID, 6),
    ("Unknown Buyer B", "unknown-buyer-b.example", "another-brand.example", None, "default", DEFAULT_ADVERTISER_ID, 11),
]


PRODUCTS: list[dict[str, object]] = [
    {
        "product_id": "demo_premium_display",
        "name": "Premium Display 300x250",
        "description": "Above-the-fold display inventory across the publisher's premium properties.",
        "format_id": "display_300x250",
    },
    {
        "product_id": "demo_video_preroll",
        "name": "Video Pre-roll 16x9",
        "description": "Skippable pre-roll inventory across long-form video.",
        "format_id": "video_16x9",
    },
    {
        "product_id": "demo_newsletter_sponsor",
        "name": "Newsletter Sponsorship",
        "description": "Sole-sponsor placement in the publisher's daily newsletter.",
        "format_id": "html_native",
    },
]


PRINCIPAL_ID = "demo-host"
PRINCIPAL_NAME = "Demo Host (Embedded)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rand_token(prefix: str, n: int = 12) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _gen_tenant_id() -> str:
    return f"demo_{uuid.uuid4().hex[:10]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _build_tenant_urls(tenant_id: str, *, localhost: bool) -> dict[str, str | dict[str, str]]:
    if localhost:
        return {
            "public_agent_url": f"http://localhost:8000/agent/{tenant_id}",
            "embed_breadcrumb_root": {
                "label": "Demo Storefront",
                "url": "http://localhost:3000/storefront/demo",
            },
        }
    return {
        "public_agent_url": f"https://demo-host.example/agent/{tenant_id}",
        "embed_breadcrumb_root": {
            "label": "Demo Storefront",
            "url": "https://demo-host.example/storefront/demo",
        },
    }


# ---------------------------------------------------------------------------
# Cleanup mode
# ---------------------------------------------------------------------------


def _clean(host_source: str) -> int:
    """Delete every tenant whose ``external_source`` matches ``host_source``.

    Refuses if any matched tenant has ``is_embedded=False`` — embedded is the
    only mode the seed script ever creates, so a non-embedded match means we'd
    be touching real data.
    """
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        # Inspect tenants via a lightweight raw query so we never trigger
        # JSONType validation on Account/etc rows (those load lazily through
        # the ORM cascade, which is exactly what we're avoiding).
        rows = session.execute(
            text("SELECT tenant_id, is_embedded FROM tenants WHERE external_source = :src"),
            {"src": host_source},
        ).all()
        if not rows:
            print(f"No tenants found with external_source={host_source!r}; nothing to clean.")
            return 0
        for tenant_id, is_embedded in rows:
            if not is_embedded:
                print(
                    f"Refusing to clean: tenant {tenant_id!r} (external_source={host_source!r}) is not embedded.",
                    file=sys.stderr,
                )
                return 1
        ids = [r[0] for r in rows]
        _delete_tenant_rows(session, ids)
        session.commit()
        print(f"Cleaned {len(ids)} tenant(s): {ids}")
        return 0


# ---------------------------------------------------------------------------
# Seed mode
# ---------------------------------------------------------------------------


def _delete_existing_tenant(session, tenant_id: str) -> bool:
    """Hard-delete a tenant + child rows. Returns True if a row was deleted.

    Uses raw SQL deletion (see ``_delete_tenant_rows``) rather than ORM
    cascade so a re-seed succeeds even when the existing rows fail JSONType
    validation on reload (e.g. pre-existing Account.brand values).
    """
    existing = session.execute(
        text("SELECT 1 FROM tenants WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    ).first()
    if existing is None:
        return False
    _delete_tenant_rows(session, [tenant_id])
    session.flush()
    return True


def _create_tenant(
    session,
    *,
    tenant_id: str,
    host_source: str,
    localhost: bool,
) -> Tenant:
    urls = _build_tenant_urls(tenant_id, localhost=localhost)
    tenant = Tenant(
        tenant_id=tenant_id,
        name="Acme Publisher (Demo)",
        subdomain=f"demo-{tenant_id}".replace("_", "-")[:90],
        is_active=True,
        is_embedded=True,
        external_source=host_source,
        external_org_id=f"org_demo_{uuid.uuid4().hex[:8]}",
        ad_server="mock",
        billing_plan="standard",
        public_agent_url=urls["public_agent_url"],
        embed_breadcrumb_root=urls["embed_breadcrumb_root"],
        default_gam_advertiser_id=DEFAULT_ADVERTISER_ID,
        authorized_emails=["demo@acme-publisher.example"],
        authorized_domains=["acme-publisher.example"],
    )
    session.add(tenant)
    session.flush()
    return tenant


def _create_currency_and_property_tag(session, tenant_id: str) -> None:
    session.add(
        CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=100,
        )
    )
    session.add(
        PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="Wildcard tag matching all publisher inventory (demo seed).",
        )
    )


def _create_adapter_config(session, tenant_id: str) -> None:
    session.add(
        AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="mock",
        )
    )


def _create_principal(session, tenant_id: str) -> Principal:
    principal = Principal(
        tenant_id=tenant_id,
        principal_id=PRINCIPAL_ID,
        name=PRINCIPAL_NAME,
        access_token=_rand_token("embedded-mode-no-token:"),
        platform_mappings={"mock": {"advertiser_id": "demo_host_adv"}},
    )
    session.add(principal)
    return principal


def _create_gam_advertisers(session, tenant_id: str) -> None:
    now = _now()
    for spec in GAM_ADVERTISERS:
        session.add(
            GamAdvertiser(
                tenant_id=tenant_id,
                advertiser_id=spec["advertiser_id"],
                name=spec["name"],
                currency_code="USD",
                status=spec["status"],
                synced_at=now,
            )
        )


def _create_routing_rules(session, tenant_id: str) -> None:
    for spec in ROUTING_RULES:
        session.add(
            AdvertiserRoutingRule(
                id=f"rule_{uuid.uuid4().hex[:12]}",
                tenant_id=tenant_id,
                principal_id=None,
                operator_domain=spec["operator_domain"],
                brand_house=spec["brand_house"],
                brand_id=spec["brand_id"],
                gam_advertiser_id=spec["gam_advertiser_id"],
            )
        )


def _create_accounts_and_buys(session, tenant_id: str, principal_id: str, days: int) -> tuple[int, int]:
    """Create Account rows + a backfill of MediaBuy rows for each.

    Returns ``(num_accounts, num_media_buys)``.
    """
    rng = random.Random(0xDEEDC0DE)  # deterministic shape across runs
    now = _now()
    window_seconds = days * 86400

    account_count = 0
    buy_count = 0

    for label, operator, brand_house, brand_id, resolved_via, advertiser_id, num_buys in ACCOUNT_SEEDS:
        account_id = f"acct_{uuid.uuid4().hex[:10]}"
        account = Account(
            tenant_id=tenant_id,
            account_id=account_id,
            name=label,
            status="active",
            operator=operator,
            brand={"domain": brand_house or "", "brand_id": brand_id} if brand_house else None,
            principal_id=principal_id,
            platform_mappings={"google_ad_manager": {"advertiser_id": advertiser_id}},
            resolved_via=resolved_via,
        )
        session.add(account)
        account_count += 1

        # Spread MediaBuy rows over the last `days` window.
        for _ in range(num_buys):
            offset = rng.randint(0, window_seconds)
            created = now - timedelta(seconds=offset)
            start = created.date()
            end = (created + timedelta(days=rng.randint(7, 30))).date()
            session.add(
                MediaBuy(
                    media_buy_id=f"mb_{uuid.uuid4().hex[:12]}",
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    order_name=f"{label} order {rng.randint(1000, 9999)}",
                    advertiser_name=label,
                    budget=rng.choice([1000, 2500, 5000, 10000, 25000]),
                    currency="USD",
                    start_date=start,
                    end_date=end,
                    start_time=created,
                    end_time=created + timedelta(days=rng.randint(7, 30)),
                    status=rng.choice(["active", "active", "active", "completed", "paused"]),
                    raw_request={
                        "packages": [{"package_id": "pkg_demo", "product_id": "demo_premium_display"}],
                        "buyer_ref": {
                            "operator": operator,
                            "brand_house": brand_house,
                            "brand_id": brand_id,
                        },
                    },
                    account_id=account_id,
                    created_at=created,
                    updated_at=created,
                )
            )
            buy_count += 1

    return account_count, buy_count


def _create_products(session, tenant_id: str) -> None:
    for spec in PRODUCTS:
        session.add(
            Product(
                tenant_id=tenant_id,
                product_id=spec["product_id"],
                name=spec["name"],
                description=spec["description"],
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": spec["format_id"]}],
                targeting_template={"geo": ["US"]},
                delivery_type="guaranteed",
                property_tags=["all_inventory"],
                is_custom=False,
            )
        )


def _seed(args: argparse.Namespace) -> int:
    tenant_id = args.tenant_id or _gen_tenant_id()
    host_source = args.host_source

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        # Idempotent re-seed: if the same tenant_id exists, drop + recreate.
        # Cascades on Tenant carry the children with it.
        was_replaced = _delete_existing_tenant(session, tenant_id)

        tenant = _create_tenant(
            session,
            tenant_id=tenant_id,
            host_source=host_source,
            localhost=args.localhost,
        )
        _create_currency_and_property_tag(session, tenant.tenant_id)
        _create_adapter_config(session, tenant.tenant_id)
        principal = _create_principal(session, tenant.tenant_id)
        _create_gam_advertisers(session, tenant.tenant_id)
        _create_routing_rules(session, tenant.tenant_id)
        _create_products(session, tenant.tenant_id)
        accounts, buys = _create_accounts_and_buys(
            session,
            tenant.tenant_id,
            principal.principal_id,
            days=args.days,
        )

        session.commit()

        # Refresh detached instance for summary printing.
        embed_url = (tenant.embed_breadcrumb_root or {}).get("url")
        agent_url = tenant.public_agent_url

    _print_summary(
        tenant_id=tenant_id,
        host_source=host_source,
        replaced=was_replaced,
        accounts=accounts,
        buys=buys,
        days=args.days,
        embed_url=embed_url,
        agent_url=agent_url,
        localhost=args.localhost,
    )
    return 0


def _print_summary(
    *,
    tenant_id: str,
    host_source: str,
    replaced: bool,
    accounts: int,
    buys: int,
    days: int,
    embed_url: str | None,
    agent_url: str | None,
    localhost: bool,
) -> None:
    # Admin UI (where the Buyer Routing pages and Tenant Management API live)
    # always points at the local dev stack — that's where this script's
    # provisioning calls land. ``--localhost`` only swaps the *tenant-side*
    # URLs (public_agent_url, embed_breadcrumb_root) between localhost and
    # the demo-host; see _create_tenant().
    base = "http://localhost:8000"
    line = "=" * 78
    print(line)
    print(f"  Demo tenant {'replaced' if replaced else 'created'}: {tenant_id}")
    print(line)
    print(f"  external_source        : {host_source}")
    print("  embedded               : True")
    print(f"  default GAM advertiser : {DEFAULT_ADVERTISER_ID}")
    print(
        f"  GAM advertisers cached : {len(GAM_ADVERTISERS)} ({sum(1 for a in GAM_ADVERTISERS if a['status'] == 'active')} active, "
        f"{sum(1 for a in GAM_ADVERTISERS if a['status'] == 'inactive')} inactive)"
    )
    print(f"  Routing rules          : {len(ROUTING_RULES)}")
    print(f"  Accounts               : {accounts}")
    print(f"  MediaBuys backfilled   : {buys} over last {days}d")
    print(f"  Products               : {len(PRODUCTS)}")
    print(f"  public_agent_url       : {agent_url}")
    print(f"  embed_breadcrumb_root  : {embed_url}")
    print()
    print("Useful URLs (assumes admin UI on http://localhost:8000):")
    print(f"  Buyer Routing UI       : {base}/tenant/{tenant_id}/settings/buyer-routing")
    print(f"  Recent buyers          : {base}/tenant/{tenant_id}/settings/buyer-routing#recent")
    print(f"  Storefront breadcrumb  : {embed_url}")
    print()
    print("Sample curl (replace MGMT_API_KEY with the configured tenant management key):")
    print(
        f"  curl -H 'X-Tenant-Management-API-Key: $MGMT_API_KEY' "
        f"{base}/admin/api/v1/tenant-management/tenants/{tenant_id}/status | jq"
    )
    print()
    print(
        f"Re-run idempotently:  uv run python scripts/seed_demo_tenant.py --tenant-id {tenant_id}"
        + (" --localhost" if localhost else "")
    )
    print(f"Clean up:             uv run python scripts/seed_demo_tenant.py --clean --host-source {host_source}")
    print(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tenant-id", default=None, help="reuse an existing demo tenant id (idempotent re-seed)")
    parser.add_argument(
        "--host-source",
        default="demo-host",
        help="external_source value on Tenant (also the --clean filter); default 'demo-host'",
    )
    parser.add_argument(
        "--localhost",
        action="store_true",
        help="rewrite https URLs to http://localhost variants for dev (Storefront on :3000)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="backfill window for MediaBuy timestamps (default 30)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove every tenant whose external_source matches --host-source, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.days <= 0:
        print(f"--days must be positive (got {args.days})", file=sys.stderr)
        return 1
    try:
        if args.clean:
            return _clean(args.host_source)
        return _seed(args)
    except Exception as exc:  # noqa: BLE001 — surface any error with stack
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
