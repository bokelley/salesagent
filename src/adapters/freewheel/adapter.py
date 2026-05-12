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

- ⏳ add_creative_assets / associate_creatives — there are two distinct
  creative APIs in FreeWheel and neither maps cleanly without scopes
  that need to be granted:

  Publisher-side (us, via Talpa's token):
    * ``PUT /services/v4/mkpl_creatives/{id}`` — unified approval verb;
      body is ``{approval_status: Approved|Rejected|Pending, approval_notes}``.
    * Sibling type-specific endpoints exist:
      ``mkpl_exchange_programmatic_creatives``,
      ``mkpl_private_direct_sold_creatives``,
      ``mkpl_private_programmatic_creatives``.
    * All 403 IAM-deny on our current token — needs publisher scope grant.
    * Note: this is an *approval* workflow, not creative creation. The
      buyer registers the creative through their DSP; it appears in the
      publisher's marketplace queue; we approve it. So AdCP's
      ``sync_creatives`` (buyer registering creatives) doesn't have a
      direct publisher-side equivalent. The adapter's approval surface
      maps to AdCP's creative review/approval flow, not its creation flow.

  Buyer-side (would need a separate Demand API token, which Talpa as a
  publisher wouldn't have):
    * ``POST /demand/v1/accounts/{seat_id}/ads`` — buyer registers a
      creative in their DSP seat. Requires ``X-Freewheel-Account-Id``.
    * Out of scope for publisher-token-driven integration.
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
from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS, FreeWheelConnectionConfig, FreeWheelProductConfig
from src.adapters.freewheel.targeting import build_targeting, validate_targeting
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

        self.api_token = self.config.get("api_token")
        self.environment = self.config.get("environment", "production")
        self.base_url = FREEWHEEL_HOSTS.get(self.environment, FREEWHEEL_HOSTS["production"])

        if self.dry_run:
            self.log("Running in dry-run mode — FreeWheel Publisher API calls will be simulated", dry_run_prefix=False)
            self._client: FreeWheelClient | None = None
        else:
            if not self.api_token:
                raise ValueError("FreeWheel config is missing 'api_token'")
            self._client = FreeWheelClient(api_token=self.api_token, base_url=self.base_url)

    # ----- capabilities -----

    def get_supported_pricing_models(self) -> set[str]:
        return {"cpm", "flat_rate"}

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        return TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            nielsen_dma=True,
        )

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
        payload: dict[str, Any] = {
            "name": package.name,
            "advertiserId": self.advertiser_id,
            "startDate": start_time.date().isoformat(),
            "endDate": end_time.date().isoformat(),
            "impressionGoal": package.impressions,
            "rate": rate,
            "rateType": rate_type,
            "placementIds": list(product_config.get("placement_ids", [])),
            "targeting": build_targeting(package.targeting_overlay, product_config),
            "externalId": package.package_id,
        }
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
