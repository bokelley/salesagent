"""FreeWheel adapter — implements ``AdServerAdapter`` against the Publisher API.

Entity mapping (from the Publisher API docs):
- AdCP MediaBuy → FreeWheel Campaign
- AdCP Package → FreeWheel Line Item
- AdCP Creative → FreeWheel Creative
- AdCP creative-to-package assignment → Creative-Line Item Association
- AdCP Product → FreeWheel Placement(s) + targeting profile

This is a skeleton: dry-run mode logs the planned API calls based on the
public Publisher API reference. Live mode currently raises a clear error
for create_media_buy and stubs the remaining methods so the adapter can be
selected and configured before staging credentials arrive. Concrete request
shapes and live-mode coverage are finalised once we can exercise the API
against a sandbox.
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
from src.adapters.freewheel.client import FreeWheelAPIError, FreeWheelClient
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
        """Resolve OAuth client credentials and the target environment host.

        Dry-run defers OAuth client construction so the adapter can be
        configured before staging credentials are provisioned by FreeWheel's
        Account Team.
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        self.advertiser_id = self.principal.get_adapter_id("freewheel") or self.config.get("default_advertiser_id")
        if not self.advertiser_id and not self.dry_run:
            raise ValueError(
                f"Principal {principal.principal_id} does not have a FreeWheel advertiser ID "
                "and no default_advertiser_id is configured"
            )

        self.client_id = self.config.get("client_id")
        self.client_secret = self.config.get("client_secret")
        self.network_id = self.config.get("network_id")
        self.environment = self.config.get("environment", "production")
        self.base_url = FREEWHEEL_HOSTS.get(self.environment, FREEWHEEL_HOSTS["production"])

        if self.dry_run:
            self.log("Running in dry-run mode — FreeWheel Publisher API calls will be simulated", dry_run_prefix=False)
            self._client: FreeWheelClient | None = None
        else:
            if not self.client_id or not self.client_secret or not self.network_id:
                raise ValueError("FreeWheel config is missing 'client_id', 'client_secret', or 'network_id'")
            self._client = FreeWheelClient(
                client_id=self.client_id,
                client_secret=self.client_secret,
                network_id=self.network_id,
                base_url=self.base_url,
            )

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

        media_buy_id = (
            f"freewheel_{request.po_number}" if request.po_number else f"freewheel_{int(datetime.now(UTC).timestamp())}"
        )

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/networks/{self.network_id}/campaigns")
            self.log(
                f"  Campaign: name=AdCP {media_buy_id}, advertiserId={self.advertiser_id}, "
                f"start={start_time.date()}, end={end_time.date()}"
            )
            for package in packages:
                rate, rate_type = self._resolve_pricing_rate(package, package_pricing_info)
                payload = self._line_item_payload(package, rate, rate_type, start_time, end_time)
                self.log(f"Would call: POST .../campaigns/{media_buy_id}/line-items")
                self.log(f"  LineItem: {payload}")
            return self._build_create_success(request, media_buy_id, packages)

        # Live mode — pending credential validation and JSON-shape lock-in.
        return self._pending_creds_error()

    # ----- creatives -----

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        if self.dry_run:
            for asset in assets:
                self.log(
                    f"Would POST {self.base_url}/networks/{self.network_id}/creatives "
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
        if self.dry_run:
            self.log(
                f"Would call: GET {self.base_url}/networks/{self.network_id}"
                f"/campaigns/{media_buy_id.replace('freewheel_', '')}"
            )
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        assert self._client is not None
        try:
            campaign = self._client.get_campaign(media_buy_id.removeprefix("freewheel_"))
            status = campaign.get("status", "active").lower()
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status)
        except FreeWheelAPIError as exc:
            logger.warning("FreeWheel get_campaign failed: %s", exc)
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
