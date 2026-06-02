"""Regression coverage for update_media_buy pause persistence (#670)."""

from __future__ import annotations

import pytest

from src.core.database.repositories import MediaBuyRepository
from src.core.schemas import GetMediaBuysRequest, MediaBuyStatus, UpdateMediaBuyRequest, UpdateMediaBuySuccess
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import AdapterConfigFactory, MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.factories.spec_required_kwargs import required_request_kwargs


@pytest.mark.requires_db
def test_pause_pending_creatives_persists_and_readback_matches(factory_session):
    tenant = TenantFactory(ad_server="mock")
    AdapterConfigFactory(tenant=tenant, adapter_type="mock")
    principal = PrincipalFactory(tenant=tenant)
    media_buy = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        status="pending_creatives",
        is_paused=False,
        revision=1,
        raw_request={},
    )
    identity = PrincipalFactory.make_identity(
        tenant_id=tenant.tenant_id,
        principal_id=principal.principal_id,
        auth_token=principal.access_token,
        tenant={"tenant_id": tenant.tenant_id, "name": tenant.name, "ad_server": "mock"},
    )

    response = _update_media_buy_impl(
        req=UpdateMediaBuyRequest(
            **required_request_kwargs(),
            media_buy_id=media_buy.media_buy_id,
            revision=1,
            paused=True,
        ),
        identity=identity,
    )

    assert isinstance(response, UpdateMediaBuySuccess)
    assert response.media_buy_status == MediaBuyStatus.paused
    assert response.revision == 2

    factory_session.expire_all()
    persisted = MediaBuyRepository(factory_session, tenant.tenant_id).get_by_id(media_buy.media_buy_id)
    assert persisted is not None
    assert persisted.status == "paused"
    assert persisted.is_paused is True
    assert persisted.revision == 2
    assert persisted.raw_request["_pause_previous_status"] == "pending_creatives"

    list_response = _get_media_buys_impl(
        req=GetMediaBuysRequest(
            media_buy_ids=[media_buy.media_buy_id],
            status_filter=[MediaBuyStatus.paused],
        ),
        identity=identity,
    )

    assert len(list_response.media_buys) == 1
    assert list_response.media_buys[0].media_buy_id == media_buy.media_buy_id
    assert list_response.media_buys[0].status == MediaBuyStatus.paused
