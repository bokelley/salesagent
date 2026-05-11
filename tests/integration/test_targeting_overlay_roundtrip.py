"""Round-trip coverage for ``Package.targeting_overlay`` on create and update paths.

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
``inventory_list_targeting/get_after_create`` step failed (PR #246 fixed it).

This module drives ``_create_media_buy_impl`` and ``_update_media_buy_impl``
end-to-end against PostgreSQL and asserts ``property_list`` /
``collection_list`` references survive both:

* ``test_property_list_and_collection_list_round_trip`` — create persists +
  ``get_media_buys`` echoes (PR #246 regression guard).
* ``test_update_overrides_targeting_overlay`` — ``update_media_buy`` rewrites
  the persisted overlay and ``get_media_buys`` echoes the new values (#316
  regression guard for storyboard ``inventory_list_targeting/get_after_update``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from adcp.types import MediaBuyStatus
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaPackage as DBMediaPackage
from src.core.schemas import (
    CreateMediaBuyRequest,
    GetMediaBuysRequest,
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
)
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories.spec_required_kwargs import required_request_kwargs
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
            **required_request_kwargs(),
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
            assert persisted_property.get("list_id") == property_list_id, (
                f"property_list.list_id missing from persisted package_config: got {persisted_overlay!r}"
            )
            persisted_collection = persisted_overlay.get("collection_list") or {}
            assert persisted_collection.get("list_id") == collection_list_id, (
                f"collection_list.list_id missing from persisted package_config: got {persisted_overlay!r}"
            )

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
        assert echoed_overlay.property_list is not None, (
            "property_list missing on echoed targeting_overlay — list-targeting specialism cannot be honored"
        )
        assert echoed_overlay.property_list.list_id == property_list_id
        assert echoed_overlay.collection_list is not None
        assert echoed_overlay.collection_list.list_id == collection_list_id

    async def test_update_overrides_targeting_overlay(self, sample_tenant, sample_principal, sample_products):
        """update_media_buy must persist new property_list / collection_list references.

        Storyboard step ``media_buy_seller/inventory_list_targeting/get_after_update``
        creates a buy with v1 references, swaps them via ``update_media_buy``, then
        asserts ``get_media_buys`` echoes the v2 references. PR #246 fixed the
        create-side persistence; this test guards the update-side equivalent (#316).
        """
        tenant_dict = _get_tenant_dict(sample_tenant["tenant_id"])
        identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

        agent_url = "https://governance.pinnacle-agency.example/"
        v1_property = "acme_outdoor_allowlist_v1"
        v1_collection = "acme_outdoor_collections_v1"
        v2_property = "acme_outdoor_no_match_v1"
        v2_collection = "acme_outdoor_no_match_collections_v1"

        # Step 1 — create with v1 references
        create_req = CreateMediaBuyRequest(
            **required_request_kwargs(),
            brand={"domain": "testbrand.com"},
            start_time=_future(1),
            end_time=_future(8),
            packages=[
                {
                    "product_id": "guaranteed_display",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "targeting_overlay": {
                        "property_list": {"agent_url": agent_url, "list_id": v1_property},
                        "collection_list": {"agent_url": agent_url, "list_id": v1_collection},
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
        assert media_buy_id

        # Resolve the system-assigned package_id (buyer doesn't see DB ids)
        with get_db_session() as session:
            packages = session.scalars(select(DBMediaPackage).where(DBMediaPackage.media_buy_id == media_buy_id)).all()
            assert packages, f"No MediaPackage rows for {media_buy_id}"
            package_id = packages[0].package_id

        # Step 2 — update with v2 references
        update_req = UpdateMediaBuyRequest(
            **required_request_kwargs(),
            media_buy_id=media_buy_id,
            packages=[
                {
                    "package_id": package_id,
                    "targeting_overlay": {
                        "property_list": {"agent_url": agent_url, "list_id": v2_property},
                        "collection_list": {"agent_url": agent_url, "list_id": v2_collection},
                    },
                }
            ],
        )
        update_resp = _update_media_buy_impl(req=update_req, identity=identity)
        # Update should not have failed — assert on the response shape rather than
        # internal status strings, since the success type varies (Success vs Error).
        assert not isinstance(update_resp, UpdateMediaBuyError), f"update_media_buy failed: {update_resp}"

        # Persistence assertion — package_config now reflects v2
        with get_db_session() as session:
            packages = session.scalars(select(DBMediaPackage).where(DBMediaPackage.media_buy_id == media_buy_id)).all()
            persisted_overlay = (packages[0].package_config or {}).get("targeting_overlay") or {}
            persisted_property = persisted_overlay.get("property_list") or {}
            assert persisted_property.get("list_id") == v2_property, (
                f"property_list.list_id not updated in package_config — "
                f"got {persisted_property.get('list_id')!r} expected {v2_property!r}; "
                f"full overlay: {persisted_overlay!r}"
            )
            persisted_collection = persisted_overlay.get("collection_list") or {}
            assert persisted_collection.get("list_id") == v2_collection, (
                f"collection_list.list_id not updated in package_config — "
                f"got {persisted_collection.get('list_id')!r} expected {v2_collection!r}; "
                f"full overlay: {persisted_overlay!r}"
            )

        # Step 3 — get_media_buys echoes the v2 references
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
        assert echoed_buy is not None
        assert echoed_buy.packages, "echoed media buy has no packages"
        echoed_overlay = echoed_buy.packages[0].targeting_overlay
        assert echoed_overlay is not None, "targeting_overlay missing on echoed package after update"
        assert echoed_overlay.property_list is not None
        assert echoed_overlay.property_list.list_id == v2_property
        assert echoed_overlay.collection_list is not None
        assert echoed_overlay.collection_list.list_id == v2_collection
