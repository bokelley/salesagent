"""Triton TAP adapter — implements ``AdServerAdapter`` against Media Buying API.

Entity mapping:
- AdCP MediaBuy → TAP Campaign (under publisher's Advertiser)
- AdCP Package → TAP Flight (with station/group/genre targeting rules)
- AdCP Creative → TAP Ad (linked to Flight)

The adapter is publisher-scoped: connection credentials log into one publisher
account, all stations under that publisher are addressable via flight
targeting. Station selection is per-product (not per-tenant).
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
from src.adapters.triton.client import TritonAPIError, TritonClient
from src.adapters.triton.schemas import TritonConnectionConfig, TritonProductConfig
from src.adapters.triton.targeting import build_targeting_rules, validate_targeting
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AffectedPackage,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    Error,
    MediaPackage,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyError,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

logger = logging.getLogger(__name__)


class TritonAdapter(AdServerAdapter):
    """Adapter for the Triton Digital TAP Media Buying API."""

    adapter_name = "triton"

    default_channels = ["streaming_audio", "podcast"]
    default_delivery_measurement = {"provider": "triton"}

    connection_config_class = TritonConnectionConfig
    product_config_class = TritonProductConfig
    # Triton publishes audio inventory only — see TritonAdapter docstring for
    # the entity model. supports_custom_targeting is true because the targeting
    # translator emits station/genre/daypart/stream-type rules from product
    # config and per-package custom["triton"] overrides — custom-KV in
    # everything but name.
    capabilities = AdapterCapabilities(
        supported_pricing_models=["cpm", "flat_rate"],
        inventory_entity_label="Stations",
        supports_inventory_sync=True,
        supports_geo_targeting=True,
        supports_custom_targeting=True,
        supports_inventory_profiles=False,
        supports_dynamic_products=False,
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
        """Resolve TAP credentials from ``config`` and lazily construct the JWT client.

        Dry-run defers client construction so the adapter can be selected
        and configured without valid publisher credentials.
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        self.advertiser_id = self.principal.get_adapter_id("triton") or self.config.get("default_advertiser_id")
        if not self.advertiser_id and not self.dry_run:
            raise ValueError(
                f"Principal {principal.principal_id} does not have a Triton advertiser ID "
                "and no default_advertiser_id is configured"
            )

        self.username = self.config.get("username")
        self.password = self.config.get("password")
        self.base_url = self.config.get("base_url", "https://mbapi.tritondigital.com")
        self.login_url = self.config.get("login_url", "https://login.tritondigital.com")
        self.auth_type = self.config.get("auth_type", "password")

        if self.dry_run:
            self.log("Running in dry-run mode — Triton TAP API calls will be simulated", dry_run_prefix=False)
            self._client: TritonClient | None = None
        else:
            if not self.username or not self.password:
                raise ValueError("Triton config is missing 'username' or 'password' (publisher credentials)")
            self._client = TritonClient(
                username=self.username,
                password=self.password,
                base_url=self.base_url,
                login_url=self.login_url,
                auth_type=self.auth_type,
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
        """Extract Triton product config from a package's implementation_config, if present."""
        impl = getattr(package, "implementation_config", None) or {}
        return impl.get("triton", impl) if isinstance(impl, dict) else {}

    def _flight_payload(
        self,
        package: MediaPackage,
        rate: float,
        rate_type: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        product_config = self._product_config_from_package(package)
        return {
            "name": package.name,
            "type": "STANDARD",
            "goal": {"type": "IMPRESSIONS", "value": package.impressions},
            "rate": rate,
            "rateType": rate_type,
            "startDate": start_time.date().isoformat(),
            "endDate": end_time.date().isoformat(),
            "targetingRules": build_targeting_rules(package.targeting_overlay, product_config),
            "externalCode": package.package_id,
        }

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

        targeting_error = self._validate_targeting_or_error(packages, validate_targeting, adapter_name="Triton")
        if targeting_error is not None:
            return targeting_error

        total_budget = 0.0
        for package in packages:
            rate, _ = self._resolve_pricing_rate(package, package_pricing_info)
            total_budget += rate * package.impressions / 1000

        media_buy_id = (
            f"triton_{request.po_number}" if request.po_number else f"triton_{int(datetime.now(UTC).timestamp())}"
        )

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/advertisers/{self.advertiser_id}/campaigns")
            self.log(
                f"  Campaign: name=AdCP {media_buy_id}, "
                f"start={start_time.date()}, end={end_time.date()}, totalBudget={total_budget:.2f}"
            )
            for package in packages:
                rate, rate_type = self._resolve_pricing_rate(package, package_pricing_info)
                payload = self._flight_payload(package, rate, rate_type, start_time, end_time)
                self.log(f"Would call: POST {self.base_url}/campaigns/{media_buy_id}/flights")
                self.log(f"  Flight: {payload}")
            return self._build_create_success(request, media_buy_id, packages)

        assert self._client is not None
        assert self.advertiser_id is not None  # __init__ enforces this when dry_run is False
        try:
            campaign = self._client.create_campaign(
                self.advertiser_id,
                {
                    "name": f"AdCP Campaign {media_buy_id}",
                    "startDate": start_time.date().isoformat(),
                    "endDate": end_time.date().isoformat(),
                    "totalBudget": total_budget,
                    "active": True,
                    "externalCode": media_buy_id,
                },
            )
            campaign_id = str(campaign.get("id") or campaign.get("Id"))
            for package in packages:
                rate, rate_type = self._resolve_pricing_rate(package, package_pricing_info)
                self._client.create_flight(
                    campaign_id, self._flight_payload(package, rate, rate_type, start_time, end_time)
                )
            return self._build_create_success(request, f"triton_{campaign_id}", packages)
        except TritonAPIError as exc:
            return CreateMediaBuyError(
                errors=[Error(code="api_error", message=str(exc), details={"body": exc.body} if exc.body else None)]
            )

    # ----- creatives -----

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Upload audio creatives and link them to flights.

        Dry-run logs the planned API calls; live mode is currently a stub
        because the Triton Ads endpoint shape varies by account configuration —
        a follow-up commit will wire this to the real Ads endpoint once we
        validate against a sandbox.
        """
        if self.dry_run:
            for asset in assets:
                self.log(
                    f"Would POST {self.base_url}/ads with audio creative '{asset.get('name')}' "
                    f"(format={asset.get('format')}) and link to flights for packages {asset.get('package_assignments', [])}"
                )
            return [AssetStatus(creative_id=a["creative_id"], status="approved") for a in assets]
        return [AssetStatus(creative_id=a["creative_id"], status="pending") for a in assets]

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """TAP associates creatives via the Ads endpoint linking creative→flight.

        Until live-mode upload lands, return ``skipped`` rather than failing
        the request — buyers see a clear no-op signal instead of an error.
        """
        return [
            {
                "line_item_id": line_item_id,
                "creative_id": creative_id,
                "status": "skipped",
                "message": "Triton creative association pending live-mode implementation",
            }
            for line_item_id in line_item_ids
            for creative_id in platform_creative_ids
        ]

    # ----- status / delivery -----

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/campaigns/{media_buy_id.replace('triton_', '')}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        assert self._client is not None
        try:
            campaign = self._client.get_campaign(media_buy_id.removeprefix("triton_"))
            status = "active" if campaign.get("active") else "paused"
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status)
        except TritonAPIError as exc:
            logger.warning("Triton get_campaign failed: %s", exc)
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Return delivery totals for a media buy.

        Live reporting via TAP requires queueing a report and polling — that
        flow lands in a follow-up. For now: dry-run returns simulated numbers,
        live mode returns zeros so the upstream poll loop sees a valid shape.
        """
        if not self.dry_run:
            return self._empty_delivery_response(media_buy_id, date_range)

        return self._simulated_delivery_response(media_buy_id, date_range, today, target_impressions=500_000, cpm=10.0)

    # Triton TAP has no native performance-index endpoint — base default applies.

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

        campaign_id = media_buy_id.removeprefix("triton_")

        if self.dry_run:
            return self._update_dry_run(action, media_buy_id, campaign_id, package_id, budget, today)

        assert self._client is not None
        try:
            return self._update_live(action, media_buy_id, campaign_id, package_id, budget, today)
        except TritonAPIError as exc:
            return UpdateMediaBuyError(errors=[Error(code="api_error", message=str(exc), details=None)])

    def _update_dry_run(
        self,
        action: str,
        media_buy_id: str,
        campaign_id: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        if action == "pause_media_buy":
            self.log(f"Would PATCH {self.base_url}/campaigns/{campaign_id} active=false")
        elif action == "resume_media_buy":
            self.log(f"Would PATCH {self.base_url}/campaigns/{campaign_id} active=true")
        elif action in {"pause_package", "resume_package"} and package_id:
            paused = action == "pause_package"
            self.log(f"Would PATCH flight (externalCode={package_id}) active={not paused}")
            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                affected_packages=[
                    AffectedPackage(
                        package_id=package_id,
                        paused=paused,
                        changes_applied=None,
                        buyer_package_ref=None,
                    )
                ],
                implementation_date=today,
            )
        elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget is not None:
            self.log(f"Would PATCH flight (externalCode={package_id}) goal={budget}")
        return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

    def _update_live(
        self,
        action: str,
        media_buy_id: str,
        campaign_id: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        assert self._client is not None
        if action in {"pause_media_buy", "resume_media_buy"}:
            self._client.update_campaign(campaign_id, {"active": action == "resume_media_buy"})
            return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

        if action in {"pause_package", "resume_package"} and package_id:
            flight = self._find_flight_by_external_code(campaign_id, package_id)
            if not flight:
                return UpdateMediaBuyError(
                    errors=[Error(code="flight_not_found", message=f"Flight '{package_id}' not found", details=None)]
                )
            paused = action == "pause_package"
            self._client.update_flight(str(flight.get("id") or flight.get("Id")), {"active": not paused})
            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                affected_packages=[
                    AffectedPackage(
                        package_id=package_id,
                        paused=paused,
                        changes_applied=None,
                        buyer_package_ref=None,
                    )
                ],
                implementation_date=today,
            )

        if action == "update_package_impressions" and package_id and budget is not None:
            flight = self._find_flight_by_external_code(campaign_id, package_id)
            if not flight:
                return UpdateMediaBuyError(
                    errors=[Error(code="flight_not_found", message=f"Flight '{package_id}' not found", details=None)]
                )
            self._client.update_flight(
                str(flight.get("id") or flight.get("Id")),
                {"goal": {"type": "IMPRESSIONS", "value": budget}},
            )

        if action == "update_package_budget":
            # Budget→impressions conversion needs the flight's actual rate from
            # TAP. Until we implement a get-flight-rate path (and confirm the
            # rate field shape in the Media Buying API), refuse rather than
            # ship the wrong impression goal under an assumed CPM.
            return UpdateMediaBuyError(
                errors=[
                    Error(
                        code="not_implemented",
                        message=(
                            "update_package_budget pending TAP flight-rate read. "
                            "Use update_package_impressions to set a goal directly."
                        ),
                        details=None,
                    )
                ]
            )

        return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

    def _find_flight_by_external_code(self, campaign_id: str, external_code: str) -> dict[str, Any] | None:
        assert self._client is not None
        for flight in self._client.list_flights(campaign_id):
            if flight.get("externalCode") == external_code or flight.get("name") == external_code:
                return flight
        return None
