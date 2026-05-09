"""Round-trip coverage for ``Package.targeting_overlay`` on the create → list path.

Per the AdCP spec (``Package.targeting_overlay`` on get_media_buys), sellers
MUST echo the persisted targeting back so buyers can verify what was stored —
including ``PropertyListReference`` / ``CollectionListReference`` for sellers
claiming the list-targeting specialisms.

PR #217 added the read-side hydration (``_build_targeting_overlay``) but its
coverage mocked ``package_config`` directly, bypassing the create path.  The
production regression observed on the Wonderstruck deployment was that the
auto-approval persistence loop pulled ``targeting_overlay`` from the adapter
response (a stripped ``ResponsePackage``), not the buyer's request — so
``property_list`` was never written and the storyboard's
``inventory_list_targeting/get_after_create`` step failed.

This test drives ``_create_media_buy_impl`` and ``_get_media_buys_impl``
end-to-end against PostgreSQL and asserts ``property_list`` / ``collection_list``
references survive the round trip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from adcp.types import MediaBuyStatus
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaPackage as DBMediaPackage
from src.core.schemas import CreateMediaBuyRequest, GetMediaBuysRequest
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_list import _get_media_buys_impl
from tests.integration.media_buy_helpers import _get_tenant_dict, make_lifecycle_identity

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.asyncio]


def _future(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


class TestTargetingOverlayRoundtrip:
    """create_media_buy → get_media_buys must preserve PropertyListReference / CollectionListReference."""

    async def test_property_list_and_collection_list_round_trip(self, sample_tenant, sample_principal, sample_products):
        """Buyer-supplied list references on create must round-trip through get_media_buys."""
        tenant_dict = _get_tenant_dict(sample_tenant["tenant_id"])
        identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

        property_agent_url = "https://governance.pinnacle-agency.example/"
        property_list_id = "acme_outdoor_allowlist_v1"
        collection_agent_url = "https://governance.pinnacle-agency.example/"
        collection_list_id = "acme_outdoor_collections_v1"

        create_req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            start_time=_future(1),
            end_time=_future(8),
            packages=[
                {
                    "product_id": "guaranteed_display",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "targeting_overlay": {
                        "property_list": {
                            "agent_url": property_agent_url,
                            "list_id": property_list_id,
                        },
                        "collection_list": {
                            "agent_url": collection_agent_url,
                            "list_id": collection_list_id,
                        },
                    },
                }
            ],
        )

        create_result = await _create_media_buy_impl(req=create_req, identity=identity)
        assert create_result.status != "failed", (
            f"create_media_buy failed: status={create_result.status}, "
            f"errors={getattr(create_result.response, 'errors', None)}"
        )
        media_buy_id = create_result.response.media_buy_id
        assert media_buy_id, f"media_buy_id missing: {create_result.response}"

        # Persistence assertion — package_config.targeting_overlay must contain the
        # buyer-supplied references. Direct DB read isolates the create-path from the
        # read-path so the failure mode is unambiguous.
        with get_db_session() as session:
            packages = session.scalars(select(DBMediaPackage).where(DBMediaPackage.media_buy_id == media_buy_id)).all()
            assert packages, f"No MediaPackage rows for {media_buy_id}"
            persisted_overlay = (packages[0].package_config or {}).get("targeting_overlay") or {}
            persisted_property = persisted_overlay.get("property_list") or {}
            assert (
                persisted_property.get("list_id") == property_list_id
            ), f"property_list.list_id missing from persisted package_config: got {persisted_overlay!r}"
            persisted_collection = persisted_overlay.get("collection_list") or {}
            assert (
                persisted_collection.get("list_id") == collection_list_id
            ), f"collection_list.list_id missing from persisted package_config: got {persisted_overlay!r}"

        # Read-path assertion — get_media_buys must echo the references back.
        # status_filter must include both pending_creatives (the variant-1
        # status emitted when create_media_buy is called without creatives;
        # see PR #196) and pending_start (used once creatives are synced and
        # the buy is waiting on its future start_time). Without the
        # pending_creatives entry the filter rejects the freshly-created
        # buy and the assertion below sees ``media_buys=[]``.
        list_req = GetMediaBuysRequest(
            media_buy_ids=[media_buy_id],
            status_filter=[
                MediaBuyStatus.pending_creatives,
                MediaBuyStatus.pending_start,
                MediaBuyStatus.active,
            ],
        )
        list_resp = _get_media_buys_impl(list_req, identity=identity)

        assert list_resp.media_buys, f"get_media_buys returned no buys: {list_resp}"
        echoed_buy = next((b for b in list_resp.media_buys if b.media_buy_id == media_buy_id), None)
        assert echoed_buy is not None, (
            f"media_buy {media_buy_id} missing from get_media_buys response: "
            f"{[b.media_buy_id for b in list_resp.media_buys]}"
        )
        assert echoed_buy.packages, "echoed media buy has no packages"
        echoed_overlay = echoed_buy.packages[0].targeting_overlay
        assert echoed_overlay is not None, "targeting_overlay missing on echoed package"
        assert (
            echoed_overlay.property_list is not None
        ), "property_list missing on echoed targeting_overlay — list-targeting specialism cannot be honored"
        assert echoed_overlay.property_list.list_id == property_list_id
        assert echoed_overlay.collection_list is not None
        assert echoed_overlay.collection_list.list_id == collection_list_id
