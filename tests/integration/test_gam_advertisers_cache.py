"""Sprint 5 piece D — GAM advertisers cache tests.

Covers the three deliverables:
1. ``sync_advertisers`` worker upserts + soft-deletes against a mocked
   GAM client.
2. ``GET /tenants/{tid}/gam/advertisers`` is searchable + paginated +
   sub-100ms on a 500-row table.
3. ``POST /tenants/{tid}/buyer-advertiser-mappings`` rejects unknown
   ``gam_advertiser_id`` when the cache is populated; accepts when
   the cache is empty (graceful degradation during onboarding).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy import select

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AdvertiserRoutingRule,
    GamAdvertiser,
    Tenant,
)
from src.services.gam_advertisers_sync import sync_advertisers
from tests.factories import AdapterConfigFactory, GamAdvertiserFactory, TenantFactory
from tests.helpers.managed_tenant_api import bind_factories_to_session, install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

API_KEY = "sk-gam-advertisers-test-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def app(integration_db, install_api_key):
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture
def bound_factories(integration_db):
    """Bind every factory to a session so tests can call ``XFactory(...)``.

    Mirrors the helper used by ``test_managed_tenant_api.py`` — keeps the
    architecture guard happy without inline ``session.add()``. Marks the
    session as a management-API caller so the embedded-tenant write
    guard accepts ``is_embedded=True`` inserts.
    """
    with bind_factories_to_session() as session:
        session.info["management_api_caller"] = True
        yield session


@pytest.fixture
def tenant_factory(integration_db, bound_factories):
    """Provision an embedded-mode tenant + GAM AdapterConfig via factories.

    Returns a callable so tests that need multiple isolated tenants get
    distinct rows; cleanup is mechanical (Tenant cascade handles
    ``gam_advertisers``, ``advertiser_routing_rules``, ``adapter_config``).
    """
    created: list[str] = []

    def _make() -> str:
        tid = f"tenant_advsync_{datetime.now(UTC).timestamp():.6f}_{len(created)}"
        TenantFactory(
            tenant_id=tid,
            name=f"Adv Sync {tid}",
            subdomain=tid.replace("_", "-").replace(".", "-"),
            ad_server="google_ad_manager",
            is_active=True,
            billing_plan="standard",
            is_embedded=True,
            external_org_id=tid,
            external_source="test",
            public_agent_url="https://test.scope3.com/agent",
            authorized_emails=["test@example.com"],
            authorized_domains=[],
            human_review_required=True,
            auto_approve_format_ids=[],
        )
        AdapterConfigFactory(
            tenant_id=tid,
            adapter_type="google_ad_manager",
            gam_network_code="12345",
        )
        created.append(tid)
        return tid

    yield _make

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for tid in created:
            session.execute(GamAdvertiser.__table__.delete().where(GamAdvertiser.tenant_id == tid))
            session.execute(AdvertiserRoutingRule.__table__.delete().where(AdvertiserRoutingRule.tenant_id == tid))
            session.execute(AdapterConfig.__table__.delete().where(AdapterConfig.tenant_id == tid))
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _mock_gam_client(advertisers: list[dict]) -> MagicMock:
    """Build a MagicMock that mimics ``ad_manager.AdManagerClient`` enough
    for ``sync_advertisers`` to page through results.

    ``advertisers`` items are dicts with id/name/status/currency_code.
    """
    page_size = 500
    pages = [advertisers[i : i + page_size] for i in range(0, len(advertisers), page_size)] or [[]]

    page_idx = {"i": 0}

    def _get_companies(_statement):
        idx = page_idx["i"]
        page = pages[idx] if idx < len(pages) else []
        page_idx["i"] += 1
        return SimpleNamespace(
            results=[
                SimpleNamespace(
                    id=int(a["id"]) if a["id"].isdigit() else a["id"],
                    name=a["name"],
                    creditStatus=a.get("status", "active"),
                )
                for a in page
            ],
            totalResultSetSize=len(advertisers),
        )

    company_service = MagicMock()
    company_service.getCompaniesByStatement = MagicMock(side_effect=_get_companies)
    client = MagicMock()
    client.GetService = MagicMock(return_value=company_service)
    return client


def _make_factory(client: MagicMock):
    return lambda _tenant_id: client


# ---------------------------------------------------------------------------
# Sync worker
# ---------------------------------------------------------------------------


class TestSyncWorker:
    def test_initial_sync_upserts_all_advertisers(self, tenant_factory):
        tid = tenant_factory()
        advertisers = [
            {"id": "1001", "name": "Acme Sports", "status": "active"},
            {"id": "1002", "name": "Beta Foods", "status": "active"},
            {"id": "1003", "name": "Gamma Pictures", "status": "active"},
        ]
        client = _mock_gam_client(advertisers)

        summary = sync_advertisers(tid, client_factory=_make_factory(client))

        assert summary["upserted"] == 3
        assert summary["soft_deleted"] == 0
        with get_db_session() as session:
            rows = session.scalars(select(GamAdvertiser).where(GamAdvertiser.tenant_id == tid)).all()
        assert {r.advertiser_id for r in rows} == {"1001", "1002", "1003"}
        assert {r.name for r in rows} == {"Acme Sports", "Beta Foods", "Gamma Pictures"}
        assert all(r.status == "active" for r in rows)

    def test_re_sync_updates_renamed_advertiser(self, tenant_factory):
        tid = tenant_factory()
        client_v1 = _mock_gam_client([{"id": "1001", "name": "Old Name"}])
        sync_advertisers(tid, client_factory=_make_factory(client_v1))

        client_v2 = _mock_gam_client([{"id": "1001", "name": "New Name"}])
        sync_advertisers(tid, client_factory=_make_factory(client_v2))

        with get_db_session() as session:
            row = session.scalars(select(GamAdvertiser).filter_by(tenant_id=tid, advertiser_id="1001")).one()
        assert row.name == "New Name"

    def test_disappeared_advertiser_is_soft_deleted_not_removed(self, tenant_factory):
        tid = tenant_factory()
        client_v1 = _mock_gam_client(
            [
                {"id": "1001", "name": "Stays"},
                {"id": "1002", "name": "Disappears"},
            ]
        )
        sync_advertisers(tid, client_factory=_make_factory(client_v1))

        client_v2 = _mock_gam_client([{"id": "1001", "name": "Stays"}])
        summary = sync_advertisers(tid, client_factory=_make_factory(client_v2))

        assert summary["soft_deleted"] == 1
        with get_db_session() as session:
            rows = session.scalars(
                select(GamAdvertiser).filter_by(tenant_id=tid).order_by(GamAdvertiser.advertiser_id)
            ).all()
        # Both rows still exist — soft delete only.
        assert len(rows) == 2
        statuses = {r.advertiser_id: r.status for r in rows}
        assert statuses["1001"] == "active"
        assert statuses["1002"] == "inactive"

    def test_pages_through_large_result_set(self, tenant_factory):
        """501 advertisers exercises the pagination loop (page size 500)."""
        tid = tenant_factory()
        many = [{"id": str(i), "name": f"Advertiser {i:04d}"} for i in range(1, 502)]
        client = _mock_gam_client(many)
        summary = sync_advertisers(tid, client_factory=_make_factory(client))
        assert summary["upserted"] == 501
        with get_db_session() as session:
            rows = session.scalars(select(GamAdvertiser).where(GamAdvertiser.tenant_id == tid)).all()
        assert len(rows) == 501

    def test_empty_result_preserves_existing_cache(self, tenant_factory):
        """A transient empty GAM response must NOT empty the cache.

        Earlier the soft-delete sweep treated zero-row responses as "every
        cached advertiser disappeared," silently emptying the Buyer Routing
        picker on a single API hiccup. The worker now skips the sweep when
        GAM reports zero advertisers.
        """
        tid = tenant_factory()
        client_full = _mock_gam_client(
            [
                {"id": "1001", "name": "Stays"},
                {"id": "1002", "name": "Also Stays"},
            ]
        )
        sync_advertisers(tid, client_factory=_make_factory(client_full))

        client_empty = _mock_gam_client([])
        summary = sync_advertisers(tid, client_factory=_make_factory(client_empty))

        assert summary["soft_deleted"] == 0
        assert summary["upserted"] == 0
        with get_db_session() as session:
            rows = session.scalars(select(GamAdvertiser).filter_by(tenant_id=tid)).all()
        # Both rows still active — the empty response did not touch them.
        assert {r.advertiser_id for r in rows} == {"1001", "1002"}
        assert all(r.status == "active" for r in rows)


# ---------------------------------------------------------------------------
# GET /gam/advertisers
# ---------------------------------------------------------------------------


def _seed_cache(tenant_id: str, rows: list[tuple[str, str]]) -> None:
    """Seed the cache directly via the factory. ``rows`` is a list of
    (id, name) tuples — handy for the picker tests that don't need to
    exercise the full sync worker.

    Resolves the existing Tenant ORM row first so the factory's
    ``tenant`` SubFactory doesn't try to spin up a fresh parent.
    """
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        assert tenant is not None, f"Tenant {tenant_id!r} must exist before seeding cache rows"
    for advertiser_id, name in rows:
        GamAdvertiserFactory(
            tenant=tenant,
            advertiser_id=advertiser_id,
            name=name,
            currency_code="USD",
            status="active",
        )


class TestListGamAdvertisers:
    def test_returns_404_for_unknown_tenant(self, client, auth_headers):
        resp = client.get(
            "/api/v1/tenant-management/tenants/tenant_missing/gam/advertisers",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "tenant_not_found"

    def test_requires_api_key(self, client, tenant_factory):
        tid = tenant_factory()
        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers")
        assert resp.status_code in (401, 403)

    def test_empty_cache_returns_empty_list_with_null_synced_at(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["advertisers"] == []
        assert body["next_cursor"] is None
        assert body["synced_at"] is None

    def test_unfiltered_returns_all_in_name_order(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        _seed_cache(tid, [("3", "Charlie"), ("1", "Alpha"), ("2", "Beta")])
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers",
            headers=auth_headers,
        )
        body = resp.get_json()
        assert [a["name"] for a in body["advertisers"]] == ["Alpha", "Beta", "Charlie"]
        assert body["synced_at"] is not None

    def test_q_substring_is_case_insensitive(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        _seed_cache(tid, [("1", "Acme Sports"), ("2", "Beta Foods"), ("3", "Acme Toys")])
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?q=ACme",
            headers=auth_headers,
        )
        body = resp.get_json()
        assert {a["id"] for a in body["advertisers"]} == {"1", "3"}

    def test_q_under_two_chars_is_unfiltered(self, client, auth_headers, tenant_factory):
        """Single-character ``q`` returns first page unfiltered (avoids
        the expensive scan from a typing first-keystroke)."""
        tid = tenant_factory()
        _seed_cache(tid, [("1", "Apple"), ("2", "Banana"), ("3", "Cherry")])
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?q=A",
            headers=auth_headers,
        )
        body = resp.get_json()
        # All three returned despite "A" matching only "Apple" — the
        # filter is bypassed for under-2-char queries.
        assert len(body["advertisers"]) == 3

    def test_q_numeric_returns_exact_id_match_only(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        _seed_cache(tid, [("1001", "Acme"), ("10010", "Acme Two"), ("999", "Other")])
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?q=1001",
            headers=auth_headers,
        )
        body = resp.get_json()
        assert len(body["advertisers"]) == 1
        assert body["advertisers"][0]["id"] == "1001"

    def test_cursor_pagination_round_trips(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        _seed_cache(tid, [(str(i), f"Adv {i:03d}") for i in range(1, 26)])

        page1 = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?limit=10",
            headers=auth_headers,
        ).get_json()
        assert len(page1["advertisers"]) == 10
        assert page1["next_cursor"] is not None

        page2 = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?limit=10&cursor={page1['next_cursor']}",
            headers=auth_headers,
        ).get_json()
        assert len(page2["advertisers"]) == 10
        assert page2["next_cursor"] is not None

        page3 = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?limit=10&cursor={page2['next_cursor']}",
            headers=auth_headers,
        ).get_json()
        assert len(page3["advertisers"]) == 5
        assert page3["next_cursor"] is None

        # Disjoint pages.
        ids_seen = {a["id"] for a in page1["advertisers"] + page2["advertisers"] + page3["advertisers"]}
        assert len(ids_seen) == 25

    def test_response_is_sub_100ms_on_500_row_cache(self, client, auth_headers, tenant_factory):
        """Perf smoke test — cache reads must stay cheap because the
        picker fires on every keystroke. 500 rows is the page-size
        ceiling, well below the realistic 10k+ network."""
        tid = tenant_factory()
        _seed_cache(tid, [(str(i), f"Adv {i:04d}") for i in range(1, 501)])
        # Warm the connection (first query in a process pays the
        # session/PG handshake; we're measuring the steady-state path).
        client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?limit=50",
            headers=auth_headers,
        )

        start = time.perf_counter()
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/gam/advertisers?limit=50",
            headers=auth_headers,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed_ms < 100, f"GET /gam/advertisers took {elapsed_ms:.1f}ms (>100ms budget)"


# ---------------------------------------------------------------------------
# POST /buyer-advertiser-mappings — validation hook
# ---------------------------------------------------------------------------


class TestRoutingRuleValidation:
    """Sprint 5: the deferred Sprint 1.8 validation lands here.

    Empty cache = graceful degradation (accepts unknown id during
    onboarding); populated cache = strict (rejects unknown id 400).
    """

    def _post_mapping(self, client, auth_headers, tid, gam_advertiser_id="555"):
        return client.post(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings",
            headers=auth_headers,
            json={
                "operator_domain": "interchange.io",
                "brand_house": "coca-cola.com",
                "brand_id": "sprite",
                "gam_advertiser_id": gam_advertiser_id,
            },
        )

    def test_empty_cache_accepts_any_well_formed_id(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        resp = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="not_in_cache")
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["gam_advertiser_id"] == "not_in_cache"

    def test_populated_cache_rejects_unknown_id_with_400(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        _seed_cache(tid, [("777", "Real Advertiser")])

        resp = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="not_in_cache")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "invalid_advertiser_id"
        assert body["details"]["gam_advertiser_id"] == "not_in_cache"

    def test_populated_cache_accepts_known_id(self, client, auth_headers, tenant_factory):
        tid = tenant_factory()
        _seed_cache(tid, [("777", "Real Advertiser")])

        resp = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="777")
        assert resp.status_code == 201
        assert resp.get_json()["gam_advertiser_id"] == "777"
