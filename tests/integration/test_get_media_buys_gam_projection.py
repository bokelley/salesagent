"""Integration tests for the GAM-orders → media_buys projection.

When an operator assigns a buyer agent to a ``GamAdvertiser`` via the
buyer-routing UI, ``get_media_buys`` should surface that advertiser's
GAM orders as projected media buys for the assigned agent. Unassigned
advertisers stay invisible to all buyers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus
from sqlalchemy.orm import Session as SASession

from src.core.database.database_session import get_engine
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetMediaBuysRequest
from src.core.tools.media_buy_list import _get_media_buys_impl
from tests.factories import (
    ALL_FACTORIES,
    GamAdvertiserFactory,
    GAMLineItemFactory,
    GAMOrderFactory,
    PrincipalFactory,
    TenantFactory,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_identity(tenant_id: str, principal_id: str) -> ResolvedIdentity:
    """Build a ResolvedIdentity and set the tenant context.

    ``_get_media_buys_impl`` calls ``get_principal_object`` which reads
    the tenant from the contextvar set by ``set_current_tenant`` at
    the transport boundary. Tests have to do that bit themselves.
    """
    from src.core.config_loader import set_current_tenant

    tenant_data = {"tenant_id": tenant_id, "adapter_type": "mock"}
    set_current_tenant(tenant_data)
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant_data,
        protocol="mcp",
        testing_context=None,
    )


@pytest.fixture
def session_bound(integration_db):
    """Bind factory-boy factories to a session against the integration DB."""
    engine = get_engine()
    session = SASession(bind=engine)
    originals = {f: f._meta.sqlalchemy_session for f in ALL_FACTORIES}
    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session
        yield session
    finally:
        for f, orig in originals.items():
            f._meta.sqlalchemy_session = orig
        session.close()


class TestProjectionVisibility:
    """Projection respects advertiser→agent assignment."""

    def test_assigned_advertiser_orders_appear_for_owner(self, session_bound):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=principal.principal_id)
        order = GAMOrderFactory(
            tenant=tenant,
            advertiser_id=advertiser.advertiser_id,
            status="APPROVED",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2099, 12, 31, tzinfo=UTC),
        )
        GAMLineItemFactory(tenant=tenant, order_id=order.order_id, status="DELIVERING")

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=_make_identity(tenant.tenant_id, principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{order.order_id}" in ids

    def test_unassigned_advertiser_orders_not_visible(self, session_bound):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        # advertiser has principal_id=None → not assigned
        advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=None)
        order = GAMOrderFactory(
            tenant=tenant,
            advertiser_id=advertiser.advertiser_id,
            status="APPROVED",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2099, 12, 31, tzinfo=UTC),
        )

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=_make_identity(tenant.tenant_id, principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{order.order_id}" not in ids

    def test_other_principals_advertiser_orders_not_visible(self, session_bound):
        tenant = TenantFactory()
        owner = PrincipalFactory(tenant=tenant)
        outsider = PrincipalFactory(tenant=tenant)
        advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=owner.principal_id)
        order = GAMOrderFactory(
            tenant=tenant,
            advertiser_id=advertiser.advertiser_id,
            status="APPROVED",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2099, 12, 31, tzinfo=UTC),
        )

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=_make_identity(tenant.tenant_id, outsider.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{order.order_id}" not in ids


class TestProjectionStatusMapping:
    """GAM order status maps to AdCP MediaBuyStatus correctly."""

    @pytest.mark.parametrize(
        "gam_status,expected",
        [
            ("PAUSED", MediaBuyStatus.paused),
            ("CANCELED", MediaBuyStatus.canceled),
            ("DELETED", MediaBuyStatus.canceled),
        ],
    )
    def test_terminal_status_short_circuits(self, session_bound, gam_status, expected):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=principal.principal_id)
        order = GAMOrderFactory(
            tenant=tenant,
            advertiser_id=advertiser.advertiser_id,
            status=gam_status,
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2099, 12, 31, tzinfo=UTC),
        )

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(status_filter=expected),
            identity=_make_identity(tenant.tenant_id, principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{order.order_id}" in ids
        projected = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{order.order_id}")
        assert projected.status == expected

    def test_approved_active_dates_yields_active(self, session_bound):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=principal.principal_id)
        order = GAMOrderFactory(
            tenant=tenant,
            advertiser_id=advertiser.advertiser_id,
            status="APPROVED",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2099, 12, 31, tzinfo=UTC),
        )

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=_make_identity(tenant.tenant_id, principal.principal_id),
        )

        projected = next((mb for mb in result.media_buys if mb.media_buy_id == f"gam_{order.order_id}"), None)
        assert projected is not None
        assert projected.status == MediaBuyStatus.active


class TestProjectionPackages:
    """Line items project as packages with stable ids."""

    def test_line_items_appear_as_packages(self, session_bound):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=principal.principal_id)
        order = GAMOrderFactory(
            tenant=tenant,
            advertiser_id=advertiser.advertiser_id,
            status="APPROVED",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2099, 12, 31, tzinfo=UTC),
        )
        li1 = GAMLineItemFactory(tenant=tenant, order_id=order.order_id, status="DELIVERING")
        li2 = GAMLineItemFactory(tenant=tenant, order_id=order.order_id, status="DELIVERING")

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=_make_identity(tenant.tenant_id, principal.principal_id),
        )

        projected = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{order.order_id}")
        package_ids = sorted([p.package_id for p in projected.packages])
        assert package_ids == sorted([f"gam_li_{li1.line_item_id}", f"gam_li_{li2.line_item_id}"])
