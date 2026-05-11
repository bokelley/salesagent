#!/usr/bin/env python3
"""Seed the default tenant with the data needed to serve a working agent.

Run after ``migrate.py`` (which creates the empty default tenant) to populate
the rows that otherwise have to be configured through the admin UI:

- ``tenants`` row: ad_server, virtual_host, public_agent_url
- ``currency_limits``: one USD row
- ``property_tags``: ``all_inventory``
- ``principals``: ``ci-test-principal`` with ``ci-test-token`` (mock advertiser)
- ``publisher_partners``: one verified demo publisher (satisfies the
  "Authorized Properties" setup gate)
- ``products`` + ``pricing_options``: one CPM display product

The result is a single-tenant stack that ``./scripts/storyboard-check.sh``
can drive end-to-end with ``ALLOW_HTTP=1``.

Idempotent — every insert uses ``ON CONFLICT DO NOTHING`` (or DO UPDATE
where the field needs to be refreshed), so re-running is safe and won't
clobber tenant edits made through the admin UI.

The companion environment variables ``ADCP_TESTING=true`` and
``ADCP_MULTI_TENANT=true`` (set in ``docker-compose.yml`` for dev) clear
the SSO and "real ad-server" setup gates so the mock adapter qualifies
as fully configured.

Usage::

    docker compose exec adcp-server python scripts/ops/seed_demo_data.py
    # or, locally:
    DATABASE_URL=postgresql://... python scripts/ops/seed_demo_data.py
"""

from __future__ import annotations

import json
import logging
import sys

from sqlalchemy import text

from src.core.database.database_session import get_db_session

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"
TEST_PRINCIPAL_ID = "ci-test-principal"
TEST_PRINCIPAL_TOKEN = "ci-test-token"  # noqa: S105 — fixed dev/CI token


def _seed(session) -> None:
    # 1) Configure the tenant for mock-adapter dev. ``virtual_host`` must be a
    # valid hostname (no port) — the publisher_domain pattern validation
    # rejects ``localhost:8000``.
    session.execute(
        text(
            """
            UPDATE tenants
            SET ad_server = COALESCE(ad_server, 'mock'),
                virtual_host = COALESCE(virtual_host, 'localhost'),
                public_agent_url = COALESCE(public_agent_url, 'http://localhost:8000')
            WHERE tenant_id = :tid
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 2) USD currency limit (required gate once ad_server is configured).
    session.execute(
        text(
            """
            INSERT INTO currency_limits (tenant_id, currency_code, min_package_budget, max_daily_package_spend)
            VALUES (:tid, 'USD', 0, 100000)
            ON CONFLICT (tenant_id, currency_code) DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 3) ``all_inventory`` property tag (referenced by the demo product).
    session.execute(
        text(
            """
            INSERT INTO property_tags (tag_id, tenant_id, name, description)
            VALUES ('all_inventory', :tid, 'All Inventory', 'All publisher inventory')
            ON CONFLICT (tag_id, tenant_id) DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 4) Test principal with a stable bearer token (matches the value used in
    # ``scripts/storyboard-check.sh`` examples and the integration test suite).
    session.execute(
        text(
            """
            INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, access_token)
            VALUES (:tid, :pid, 'CI Test Principal', CAST(:mappings AS jsonb), :token)
            ON CONFLICT (tenant_id, principal_id) DO UPDATE SET access_token = EXCLUDED.access_token
            """
        ),
        {
            "tid": DEFAULT_TENANT_ID,
            "pid": TEST_PRINCIPAL_ID,
            "mappings": json.dumps({"mock": {"advertiser_id": "test-advertiser"}}),
            "token": TEST_PRINCIPAL_TOKEN,
        },
    )

    # 5) Publisher partner (satisfies the "Authorized Properties" setup gate).
    session.execute(
        text(
            """
            INSERT INTO publisher_partners (
                tenant_id, publisher_domain, display_name, is_verified,
                sync_status, total_properties, authorized_properties
            )
            VALUES (:tid, 'demo.example.com', 'Demo Publisher', true, 'success', 1, 1)
            ON CONFLICT DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 6) One demo product so ``get_products`` returns something non-empty.
    session.execute(
        text(
            """
            INSERT INTO products (
                tenant_id, product_id, name, description, format_ids, targeting_template,
                delivery_type, property_tags, delivery_measurement
            ) VALUES (
                :tid, 'demo_display_300x250', 'Demo Display 300x250',
                'Demo run-of-network display product (300x250)',
                CAST(:fmt_ids AS jsonb), CAST('{}' AS jsonb), 'guaranteed',
                CAST(:tags AS jsonb), CAST(:dm AS jsonb)
            )
            ON CONFLICT (tenant_id, product_id) DO NOTHING
            """
        ),
        {
            "tid": DEFAULT_TENANT_ID,
            "fmt_ids": json.dumps([{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]),
            "tags": json.dumps(["all_inventory"]),
            "dm": json.dumps({"provider": "publisher", "notes": "Demo measurement"}),
        },
    )

    # 7) Pricing option for the demo product (DB constraint requires at least one).
    session.execute(
        text(
            """
            INSERT INTO pricing_options (tenant_id, product_id, pricing_model, rate, currency, is_fixed)
            VALUES (:tid, 'demo_display_300x250', 'cpm', 5.00, 'USD', true)
            ON CONFLICT DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with get_db_session() as session:
        _seed(session)
        session.commit()  # get_db_session does not auto-commit
    logger.info("Demo data seeded for tenant '%s'.", DEFAULT_TENANT_ID)
    logger.info("Test bearer token: %s", TEST_PRINCIPAL_TOKEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
