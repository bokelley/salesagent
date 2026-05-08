"""Integration tests for #101 webhook delivery visibility.

Covers:
- ``DeliveryRepository.create_log`` truncation of oversized request_payload
  and response_body (64KB cap).
- ``DeliveryRepository.list_logs_for_buyer`` / ``list_logs_for_operator``
  ordering, principal scoping, and limit handling.
- ``get_media_buys`` ``ext.psa.include_webhook_activity`` opt-in surfaces
  the recent deliveries on each returned buy, scoped to the calling
  principal.
- Response shape for the webhook_deliveries entries (fields present,
  truncation visible, no leakage of other principals' rows).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.database.repositories.delivery import DeliveryRepository
from src.core.schemas import GetMediaBuysRequest
from src.core.tools.media_buy_list import _get_media_buys_impl
from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.integration._gam_projection_helpers import make_identity

# ``factory_session`` fixture is provided by tests/integration/conftest.py.

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_log(
    repo: DeliveryRepository,
    *,
    principal_id: str,
    media_buy_id: str,
    status: str = "success",
    attempt: int = 1,
    sequence_number: int = 1,
    request_payload: dict | None = None,
    response_body: str | None = None,
    http_status_code: int | None = 200,
    error_message: str | None = None,
    webhook_url: str = "https://buyer.example.com/webhook",
) -> str:
    log_id = str(uuid4())
    repo.create_log(
        log_id=log_id,
        principal_id=principal_id,
        media_buy_id=media_buy_id,
        webhook_url=webhook_url,
        task_type="delivery_report",
        status=status,
        attempt_count=attempt,
        sequence_number=sequence_number,
        notification_type="scheduled",
        http_status_code=http_status_code,
        error_message=error_message,
        request_payload=request_payload,
        response_body=response_body,
    )
    return log_id


class TestDeliveryRepositoryTruncation:
    """64KB truncation enforced at insert time."""

    def test_oversized_request_payload_is_truncated_with_marker(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        # Build a payload that JSON-encodes to >64KB.
        oversized = {"data": "x" * 80_000}

        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        log_id = _seed_log(
            repo,
            principal_id=principal.principal_id,
            media_buy_id=buy.media_buy_id,
            request_payload=oversized,
        )
        factory_session.commit()

        # Sentinel namespaces under ``_meta`` so consumers can
        # distinguish "real payload" from "metadata replacement" by
        # checking ``_meta`` membership rather than guessing from
        # leaked underscore-prefixed keys.
        logs = repo.list_logs_for_operator(buy.media_buy_id, limit=1)
        stored = logs[0]
        assert stored.request_payload is not None
        assert "_meta" in stored.request_payload
        meta = stored.request_payload["_meta"]
        assert meta.get("truncated") is True
        assert meta.get("original_size_bytes", 0) > 64 * 1024
        assert "preview" in meta

    def test_small_request_payload_passes_through(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        small = {"hello": "world"}

        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        _seed_log(
            repo,
            principal_id=principal.principal_id,
            media_buy_id=buy.media_buy_id,
            request_payload=small,
        )
        factory_session.commit()

        stored = repo.list_logs_for_operator(buy.media_buy_id, limit=1)[0]
        assert stored.request_payload == small

    def test_oversized_response_body_is_truncated(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        oversized_body = "y" * 80_000

        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        _seed_log(
            repo,
            principal_id=principal.principal_id,
            media_buy_id=buy.media_buy_id,
            response_body=oversized_body,
        )
        factory_session.commit()

        stored = repo.list_logs_for_operator(buy.media_buy_id, limit=1)[0]
        assert stored.response_body is not None
        assert len(stored.response_body.encode("utf-8")) <= 64 * 1024 + 100  # +marker
        assert stored.response_body.endswith("[truncated]")


class TestListLogsForMediaBuy:
    """Repository read method semantics."""

    def test_orders_most_recent_first(self, factory_session):
        """Most-recent first when timestamps actually differ.

        Postgres' ``func.now()`` resolves to the same value for rows
        inserted in the same transaction, so we explicitly stagger
        ``created_at`` to verify the ORDER BY rather than insertion
        order. In production webhook fires are seconds-to-minutes
        apart, so the natural ordering holds.
        """
        from datetime import UTC, datetime, timedelta

        from src.core.database.models import WebhookDeliveryLog

        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        base_time = datetime.now(UTC)
        for seq, offset_seconds in [(1, 0), (2, 60), (3, 120)]:
            log_id = _seed_log(
                repo,
                principal_id=principal.principal_id,
                media_buy_id=buy.media_buy_id,
                sequence_number=seq,
            )
            # Override server-default created_at with explicit staggered times.
            row = factory_session.get(WebhookDeliveryLog, log_id)
            row.created_at = base_time + timedelta(seconds=offset_seconds)
        factory_session.commit()

        rows = repo.list_logs_for_operator(buy.media_buy_id, limit=10)
        assert [r.sequence_number for r in rows] == [3, 2, 1]

    def test_filters_by_principal_id(self, factory_session):
        tenant = TenantFactory()
        owner = PrincipalFactory(tenant=tenant)
        outsider = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=owner)
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        _seed_log(repo, principal_id=owner.principal_id, media_buy_id=buy.media_buy_id)
        _seed_log(repo, principal_id=outsider.principal_id, media_buy_id=buy.media_buy_id)
        factory_session.commit()

        rows = repo.list_logs_for_buyer(buy.media_buy_id, owner.principal_id)
        assert len(rows) == 1
        assert rows[0].principal_id == owner.principal_id

    def test_respects_limit(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        for seq in range(1, 11):
            _seed_log(repo, principal_id=principal.principal_id, media_buy_id=buy.media_buy_id, sequence_number=seq)
        factory_session.commit()

        rows = repo.list_logs_for_operator(buy.media_buy_id, limit=3)
        assert len(rows) == 3


class TestGetMediaBuysExtPsaWebhookActivity:
    """The opt-in surface on get_media_buys."""

    def test_default_no_ext_psa_means_no_webhook_activity(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        _seed_log(repo, principal_id=principal.principal_id, media_buy_id=buy.media_buy_id)
        factory_session.commit()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(tenant.tenant_id, principal.principal_id),
        )
        returned = next((mb for mb in result.media_buys if mb.media_buy_id == buy.media_buy_id), None)
        assert returned is not None
        assert returned.ext is None or "psa" not in (returned.ext or {})

    def test_opt_in_surfaces_webhook_deliveries(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        _seed_log(
            repo,
            principal_id=principal.principal_id,
            media_buy_id=buy.media_buy_id,
            sequence_number=1,
            request_payload={"hello": "world"},
            response_body='{"ack": true}',
        )
        factory_session.commit()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(ext={"psa": {"include_webhook_activity": True}}),
            identity=make_identity(tenant.tenant_id, principal.principal_id),
        )
        returned = next(mb for mb in result.media_buys if mb.media_buy_id == buy.media_buy_id)
        assert returned.ext is not None
        deliveries = returned.ext["psa"]["webhook_deliveries"]
        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery["sequence_number"] == 1
        assert delivery["status"] == "success"
        assert delivery["http_status_code"] == 200
        assert delivery["request_payload"] == {"hello": "world"}
        assert delivery["response_body"] == '{"ack": true}'
        assert delivery["task_type"] == "delivery_report"

    def test_other_principal_deliveries_filtered_out(self, factory_session):
        tenant = TenantFactory()
        owner = PrincipalFactory(tenant=tenant)
        outsider = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=owner, status="active")
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        _seed_log(repo, principal_id=outsider.principal_id, media_buy_id=buy.media_buy_id, sequence_number=42)
        factory_session.commit()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(ext={"psa": {"include_webhook_activity": True}}),
            identity=make_identity(tenant.tenant_id, owner.principal_id),
        )
        returned = next(mb for mb in result.media_buys if mb.media_buy_id == buy.media_buy_id)
        assert returned.ext["psa"]["webhook_deliveries"] == []

    def test_buyer_surface_redacts_url_query_and_includes_delivery_id(self, factory_session):
        """Buyer surface strips the query string from webhook_url and exposes
        ``delivery_id`` so retries can be correlated."""
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        log_id = _seed_log(
            repo,
            principal_id=principal.principal_id,
            media_buy_id=buy.media_buy_id,
            webhook_url="https://buyer.example.com/hook?token=secret123&sig=abc",
        )
        factory_session.commit()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(ext={"psa": {"include_webhook_activity": True}}),
            identity=make_identity(tenant.tenant_id, principal.principal_id),
        )
        returned = next(mb for mb in result.media_buys if mb.media_buy_id == buy.media_buy_id)
        delivery = returned.ext["psa"]["webhook_deliveries"][0]
        assert delivery["url"] == "https://buyer.example.com/hook"
        assert "token" not in delivery["url"]
        assert delivery["delivery_id"] == log_id

    def test_limit_param_caps_result_size(self, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        for seq in range(1, 11):
            _seed_log(repo, principal_id=principal.principal_id, media_buy_id=buy.media_buy_id, sequence_number=seq)
        factory_session.commit()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(
                ext={"psa": {"include_webhook_activity": True, "webhook_activity_limit": 3}},
            ),
            identity=make_identity(tenant.tenant_id, principal.principal_id),
        )
        returned = next(mb for mb in result.media_buys if mb.media_buy_id == buy.media_buy_id)
        assert len(returned.ext["psa"]["webhook_deliveries"]) == 3


class TestRetentionScript:
    """``scripts/ops/cleanup_webhook_deliveries.py`` deletes old rows."""

    def test_deletes_rows_older_than_retention(self, factory_session):
        from datetime import UTC, datetime, timedelta

        from scripts.ops.cleanup_webhook_deliveries import cleanup
        from src.core.database.models import WebhookDeliveryLog

        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        repo = DeliveryRepository(factory_session, tenant.tenant_id)

        # One fresh row + one ancient row.
        fresh_id = _seed_log(repo, principal_id=principal.principal_id, media_buy_id=buy.media_buy_id)
        old_id = _seed_log(repo, principal_id=principal.principal_id, media_buy_id=buy.media_buy_id)
        factory_session.flush()
        old_row = factory_session.get(WebhookDeliveryLog, old_id)
        old_row.created_at = datetime.now(UTC) - timedelta(days=45)
        factory_session.commit()
        factory_session.close()

        deleted = cleanup(retention_days=30, dry_run=False)
        assert deleted == 1

        # Re-open a session to confirm only the old row was pruned.
        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine

        engine = get_engine()

        with SASession(bind=engine) as confirmation_session:
            assert confirmation_session.get(WebhookDeliveryLog, fresh_id) is not None
            assert confirmation_session.get(WebhookDeliveryLog, old_id) is None

    def test_dry_run_counts_without_deleting(self, factory_session):
        from datetime import UTC, datetime, timedelta

        from scripts.ops.cleanup_webhook_deliveries import cleanup
        from src.core.database.models import WebhookDeliveryLog

        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        buy = MediaBuyFactory(tenant=tenant, principal=principal)
        repo = DeliveryRepository(factory_session, tenant.tenant_id)
        old_id = _seed_log(repo, principal_id=principal.principal_id, media_buy_id=buy.media_buy_id)
        factory_session.flush()
        old_row = factory_session.get(WebhookDeliveryLog, old_id)
        old_row.created_at = datetime.now(UTC) - timedelta(days=45)
        factory_session.commit()
        factory_session.close()

        count = cleanup(retention_days=30, dry_run=True)
        assert count == 1

        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine

        engine = get_engine()

        with SASession(bind=engine) as confirmation_session:
            # Row still present — dry run didn't delete.
            assert confirmation_session.get(WebhookDeliveryLog, old_id) is not None
