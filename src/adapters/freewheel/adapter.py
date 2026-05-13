"""FreeWheel adapter — implements ``AdServerAdapter`` against the Publisher API.

Entity mapping (Mapping A — see docs/adapters/freewheel/):
- AdCP MediaBuy → FreeWheel Insertion Order (the commercial transaction:
  carries budget, schedule, currency, stage)
- AdCP Package  → FreeWheel Placement (the delivery unit, one per package)
- FW Campaign   → per-buy wrapper above the IO (auto-created; carries
  ``advertiser_id`` and groups the IO + its placements)

FreeWheel's data model is three levels (Campaign > IO > Placement). The IO
is the unit of commerce; the Campaign is a grouping layer above. Reusing a
single Campaign across many IOs (the publisher-ideal pattern) would require
state we don't currently have, so v1 creates one Campaign per AdCP MediaBuy.

Live coverage:
- ✅ create_media_buy — creates Campaign + IO + Placement(s) and returns
  the IO id as ``media_buy_id``.
- ✅ check_media_buy_status — reads the IO (not the Campaign).
- ⏳ update_media_buy — write paths verified against the live API and
  available as ``client.commercial.update_insertion_order`` /
  ``update_placement``. Adapter wiring is blocked on two data-model
  gaps that need state we don't currently keep or scopes we don't have:

  1. Per-package pause/resume needs to look up the FW placement_id from
     the AdCP package_id. The v3 placements endpoint does not honour an
     ``?insertion_order_id=X`` filter (returns the full network list)
     and there's no nested-collection endpoint at v3
     (``/insertion_orders/{id}/placements`` returns 404). The v4 nested
     form exists (``/services/v4/insertion_orders/{id}/placements``) but
     our token gets a 403 IAM deny on it — needs publisher scope grant.

  2. Per-package budget changes aren't directly representable: FW's
     budget lives on the IO, not on the placement. update_package_budget
     would either need a one-IO-per-package mapping (a different Mapping
     than A) or per-package budget tracking we don't have.

- 🟡 add_creative_assets — partial unblock as of 2026-05-12. Creative
  records themselves are reachable; the placement linkage is not:

    * ✅ ``/services/v4/creative_resources`` (CRUD verified) — manage
      creative records: name, base_ad_unit, renditions (VAST tag URIs or
      hosted content), advertiser scoping. Exposed on the client at
      ``client.creatives``.
    * ❌ ``/services/v4/creative_instances`` (403 IAM-deny) — the
      creative-to-placement association. Without scope here, creatives
      we create are orphans (they exist but don't deliver against any
      placement).
    * Marketplace creative approval (``mkpl_creatives``, PUT for
      Approved/Rejected/Pending) also 403 IAM-deny. That's a separate
      moderation flow for buyer-uploaded creatives.

  AdCP semantic note: ``sync_creatives`` (buyer registering creatives)
  partially maps via ``creative_resources`` create. But without
  ``creative_instances`` to attach them to a placement, the adapter
  can't complete the round-trip.

  Demand-side path (out of scope for publisher integration): a buyer
  with their own DSP seat would POST to
  ``/demand/v1/accounts/{seat_id}/ads`` using a separate Demand API
  bearer Talpa doesn't have.
- ⏳ get_media_buy_delivery — reporting lives on a different API surface
  not yet mapped.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.adapters.base import (
    AdapterCapabilities,
    AdServerAdapter,
    CreativeEngineAdapter,
    TargetingCapabilities,
)
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.adapters.freewheel.client import FreeWheelClient, FreeWheelError
from src.adapters.freewheel.formats import freewheel_creative_formats
from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS, FreeWheelConnectionConfig, FreeWheelProductConfig
from src.adapters.freewheel.targeting import build_targeting, validate_targeting
from src.core.database.database_session import get_db_session
from src.core.database.repositories.freewheel_inventory import FreeWheelInventoryRepository
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    Error,
    MediaPackage,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

logger = logging.getLogger(__name__)


class FreeWheelAdapter(AdServerAdapter):
    """Adapter for the FreeWheel Publisher API (Comcast Technology Solutions)."""

    adapter_name = "freewheel"

    # FreeWheel's strength is video — OLV + CTV — with display as a secondary surface.
    default_channels = ["olv", "ctv", "display"]
    default_delivery_measurement = {"provider": "freewheel"}

    connection_config_class = FreeWheelConnectionConfig
    product_config_class = FreeWheelProductConfig
    capabilities = AdapterCapabilities(
        supports_inventory_sync=True,
        supports_inventory_profiles=True,
        inventory_entity_label="Placements",
        supports_custom_targeting=True,
        supports_geo_targeting=True,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=False,
    )

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        """Resolve the bearer token and target environment host.

        Dry-run defers client construction so the adapter can be configured
        before a bearer token is provisioned by FreeWheel (or the publisher).
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        self.advertiser_id = self.principal.get_adapter_id("freewheel") or self.config.get("default_advertiser_id")
        if not self.advertiser_id and not self.dry_run:
            raise ValueError(
                f"Principal {principal.principal_id} does not have a FreeWheel advertiser ID "
                "and no default_advertiser_id is configured"
            )

        self.username = self.config.get("username")
        self.password = self.config.get("password")
        self.api_token = self.config.get("api_token")
        self.environment = self.config.get("environment", "production")
        self.base_url = FREEWHEEL_HOSTS.get(self.environment, FREEWHEEL_HOSTS["production"])

        if self.dry_run:
            self.log("Running in dry-run mode — FreeWheel Publisher API calls will be simulated", dry_run_prefix=False)
            self._client: FreeWheelClient | None = None
        else:
            has_password_grant = bool(self.username) and bool(self.password)
            has_token = bool(self.api_token)
            if not has_password_grant and not has_token:
                raise ValueError("FreeWheel config requires either (username + password) or api_token")
            self._client = FreeWheelClient(
                username=self.username,
                password=self.password,
                api_token=self.api_token,
                base_url=self.base_url,
            )

    # ----- capabilities -----

    def get_supported_pricing_models(self) -> set[str]:
        return {"cpm", "flat_rate"}

    def get_creative_formats(self) -> list[dict[str, Any]]:
        """Return the static set of VAST video formats this adapter supports.

        FreeWheel delivers video via VAST tag forwarding — the six declared
        formats cover the common pre/mid/post-roll × 15s/30s combinations.
        See :mod:`._formats` for the canonical list and the rationale for
        declaring statically rather than synthesising from synced data.
        """
        return freewheel_creative_formats(self.tenant_id)

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        return TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            nielsen_dma=True,
        )

    async def get_available_inventory(self) -> dict[str, Any]:
        """Surface the locally-synced FW taxonomy for AI product configuration.

        Reads from the ``freewheel_inventory`` cache (refreshed via the
        Sync Inventory button or :class:`FreeWheelInventorySync`). No FW
        API calls happen here — everything is served from the local cache
        so the AI product configurator can run offline.

        Shape follows the base ``get_available_inventory`` contract:

        * ``placements`` — FW ``ad_unit_packages`` (the buyer-facing bundles)
        * ``ad_units``   — FW sites + site_sections (where ads can run)
        * ``targeting_options`` — ``standard_attributes`` grouped by parent
          taxonomy key (genres, tv_ratings, languages, device_types, …)
        * ``creative_specs`` — the static VAST format declarations
        * ``properties`` — counts and metadata about the synced cache
        """
        with get_db_session() as session:
            repo = FreeWheelInventoryRepository(session, self.tenant_id or "default")
            sites = repo.list_by_type("site")
            site_sections = repo.list_by_type("site_section")
            video_groups = repo.list_by_type("video_group")
            series = repo.list_by_type("series")
            ad_unit_packages = repo.list_by_type("ad_unit_package")
            standard_attrs = repo.list_by_type("standard_attribute")

            placements = [
                {
                    "id": f"ad_unit_package:{row.entity_id}",
                    "name": row.name or row.entity_id,
                    "type": "ad_unit_package",
                }
                for row in ad_unit_packages
            ]

            ad_units = [
                {"path": f"site:{row.entity_id}", "name": row.name or row.entity_id, "type": "site"} for row in sites
            ] + [
                {
                    "path": f"site_section:{row.entity_id}",
                    "name": row.name or row.entity_id,
                    "type": "site_section",
                    "parent": row.parent_id,
                }
                for row in site_sections
            ]

            targeting_options: dict[str, list[dict[str, Any]]] = {}
            for row in standard_attrs:
                bucket = row.parent_id or "uncategorized"
                targeting_options.setdefault(bucket, []).append(
                    {"id": row.entity_id, "name": row.name or row.entity_id}
                )

            properties = {
                "sites_count": len(sites),
                "site_sections_count": len(site_sections),
                "series_count": len(series),
                "video_groups_count": len(video_groups),
                "ad_unit_packages_count": len(ad_unit_packages),
                "standard_attributes_count": len(standard_attrs),
            }

        return {
            "placements": placements,
            "ad_units": ad_units,
            "targeting_options": targeting_options,
            "creative_specs": freewheel_creative_formats(self.tenant_id),
            "properties": properties,
        }

    # ----- helpers -----

    def _product_config_from_package(self, package: MediaPackage) -> dict[str, Any]:
        impl = getattr(package, "implementation_config", None) or {}
        return impl.get("freewheel", impl) if isinstance(impl, dict) else {}

    def _line_item_payload(
        self,
        package: MediaPackage,
        rate: float,
        rate_type: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        product_config = self._product_config_from_package(package)
        # Dry-run payload — surfaces what the adapter would send to FW.
        # FreeWheel inventory targeting (sites, video_groups, series, ad_unit_package)
        # ultimately becomes ad_unit_nodes attached to the placement; that write
        # path is blocked on the v4 ``ad_unit_nodes`` scope, so for now we just
        # echo the configured intent.
        # Targeting fields that are list-shaped — echo every configured
        # dimension into the dry-run payload so operators can verify intent.
        list_dimensions = (
            "site_ids",
            "site_section_ids",
            "video_group_ids",
            "series_ids",
            "viewership_profile_ids",
            "audience_item_ids",
            "genre_ids",
            "content_daypart_ids",
            "content_duration_ids",
            "content_territory_ids",
            "language_ids",
            "device_type_ids",
            "os_ids",
            "environment_ids",
            "stream_type_ids",
            "subscription_model_ids",
            "addressability_ids",
            "privacy_signal_ids",
            "tv_rating_ids",
        )
        payload: dict[str, Any] = {
            "name": package.name,
            "advertiser_id": self.advertiser_id,
            "start_date": start_time.date().isoformat(),
            "end_date": end_time.date().isoformat(),
            "impression_goal": package.impressions,
            "rate": rate,
            "rate_type": rate_type,
            "ad_unit_package_id": product_config.get("ad_unit_package_id"),
            "price_model": product_config.get("price_model"),
            "targeting": build_targeting(package.targeting_overlay, product_config),
            "external_id": package.package_id,
        }
        for dim in list_dimensions:
            payload[dim] = list(product_config.get(dim, []))
        if product_config.get("priority") is not None:
            payload["priority"] = product_config["priority"]
        return payload

    def _pending_creds_error(self, code: str = "pending_credentials") -> CreateMediaBuyError:
        return CreateMediaBuyError(
            errors=[
                Error(
                    code=code,
                    message=(
                        "FreeWheel live-mode operations require staging or production credentials "
                        "and a sandbox-validated JSON shape. Run in dry-run mode until provisioning "
                        "completes."
                    ),
                    details=None,
                )
            ]
        )

    # ----- create_media_buy -----

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        self._audit_create_media_buy(request, start_time, end_time)

        targeting_error = self._validate_targeting_or_error(packages, validate_targeting, adapter_name="FreeWheel")
        if targeting_error is not None:
            return targeting_error

        buy_name = self._buy_name(request)

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/services/v3/campaign")
            self.log(f"  Campaign: name={buy_name}, advertiser_id={self.advertiser_id}")
            self.log(f"Would call: POST {self.base_url}/services/v3/insertion_order")
            self.log(
                f"  InsertionOrder: name={buy_name}, campaign_id=<new>, start={start_time.date()}, end={end_time.date()}"
            )
            for package in packages:
                rate, rate_type = self._resolve_pricing_rate(package, package_pricing_info)
                payload = self._line_item_payload(package, rate, rate_type, start_time, end_time)
                self.log(f"Would call: POST {self.base_url}/services/v3/placement")
                self.log(f"  Placement: {payload}")
            return self._build_create_success(request, f"freewheel_{buy_name}", packages)

        # Live mode — Mapping A: Campaign(wrapper) > IO(buy) > Placement(packages).
        assert self._client is not None
        assert self.advertiser_id is not None  # enforced in __init__ for non-dry-run
        try:
            campaign = self._client.commercial.create_campaign(name=buy_name, advertiser_id=int(self.advertiser_id))
            io = self._client.commercial.create_insertion_order(name=buy_name, campaign_id=campaign.id)
            for package in packages:
                self._client.commercial.create_placement(
                    name=package.name or package.package_id,
                    insertion_order_id=io.id,
                )
        except FreeWheelError as exc:
            logger.warning("FreeWheel create_media_buy failed: %s body=%s", exc, exc.body)
            return CreateMediaBuyError(
                errors=[
                    Error(
                        code="upstream_error",
                        message=f"FreeWheel rejected the request: {exc}",
                        details=None,
                    )
                ]
            )

        return self._build_create_success(request, f"freewheel_{io.id}", packages)

    def _buy_name(self, request: CreateMediaBuyRequest) -> str:
        """Derive a human-readable buy name from the AdCP request.

        Uses po_number when present (the buyer's reference), otherwise falls
        back to a timestamp so we don't collide if a buyer issues multiple
        buys without po_numbers.
        """
        if request.po_number:
            return f"adcp_{request.po_number}"
        return f"adcp_{int(datetime.now(UTC).timestamp())}"

    # ----- creatives -----

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        if self.dry_run:
            for asset in assets:
                self.log(
                    f"Would POST {self.base_url}/services/v3/creative "
                    f"name={asset.get('name')} format={asset.get('format')}"
                )
                self.log(f"  Then POST creative-association for line items {asset.get('package_assignments', [])}")
            return [AssetStatus(creative_id=a["creative_id"], status="approved") for a in assets]
        return [AssetStatus(creative_id=a["creative_id"], status="pending") for a in assets]

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        if self.dry_run:
            for li in line_item_ids:
                for ci in platform_creative_ids:
                    self.log(f"Would POST .../line-items/{li}/creative-associations with creativeId={ci}")
            return [
                {"line_item_id": li, "creative_id": ci, "status": "success"}
                for li in line_item_ids
                for ci in platform_creative_ids
            ]
        return [
            {
                "line_item_id": li,
                "creative_id": ci,
                "status": "skipped",
                "message": "FreeWheel creative association pending live-mode implementation",
            }
            for li in line_item_ids
            for ci in platform_creative_ids
        ]

    # ----- status / delivery -----

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        io_id = media_buy_id.removeprefix("freewheel_")
        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/services/v3/insertion_orders/{io_id}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        assert self._client is not None
        try:
            io = self._client.commercial.get_insertion_order(int(io_id))
            # The IO carries its booking state on ``stage`` (NOT_BOOKED, BOOKED, etc.);
            # ``status`` is reserved for placement/campaign-level lifecycle.
            status_value = (io.stage or io.status or "active").lower()
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status_value)
        except FreeWheelError as exc:
            logger.warning("FreeWheel get_insertion_order failed: %s", exc)
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Return delivery totals.

        Live-mode reporting requires hitting FreeWheel's separate reporting
        API (a different surface from the Publisher API). Skeleton-only:
        dry-run returns simulated numbers, live mode returns zeros until
        the reporting flow is wired.
        """
        if not self.dry_run:
            return self._empty_delivery_response(media_buy_id, date_range)

        return self._simulated_delivery_response(
            media_buy_id, date_range, today, target_impressions=750_000, cpm=18.0, completion_rate=0.85
        )

    # FreeWheel performance index updates aren't yet wired — base default applies.

    # ----- update_media_buy -----

    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        if action not in REQUIRED_UPDATE_ACTIONS:
            return self._unsupported_action_error(action)

        if self.dry_run:
            campaign_id = media_buy_id.removeprefix("freewheel_")
            if action == "pause_media_buy":
                self.log(f"Would PATCH .../campaigns/{campaign_id} status=paused")
            elif action == "resume_media_buy":
                self.log(f"Would PATCH .../campaigns/{campaign_id} status=active")
            elif action in {"pause_package", "resume_package"} and package_id:
                self.log(f"Would PATCH line item externalId={package_id} status={action}")
            elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget:
                self.log(f"Would PATCH line item externalId={package_id} goal={budget}")
            return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

        # Live mode — pending credential validation.
        from src.core.schemas import UpdateMediaBuyError

        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="pending_credentials",
                    message="FreeWheel live-mode update_media_buy pending sandbox validation",
                    details=None,
                )
            ]
        )
