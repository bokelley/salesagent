"""SpringServe adapter -- implements ``AdServerAdapter`` against the
SpringServe (Magnite) ad-server REST API.

Entity mapping (Mapping A -- see docs/adapters/springserve/):

- AdCP MediaBuy -> SpringServe Campaign (the commercial container: carries
  rate, budget, schedule)
- AdCP Package  -> SpringServe Demand Tag (the active delivery unit: pacing,
  targeting, creative binding)
- AdCP Creative -> SpringServe Video/Audio Creative (POST /api/v0/videos)
  OR VAST URL stored directly on the demand tag

SpringServe has no "Insertion Order" layer above Campaign -- the Campaign
IS the buy. We do not synthesise an IO equivalent.

Stage 1 scope (this commit): skeleton + auth + dry-run for every required
method. Live writes land in Stage 2.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.adapters.base import (
    AdapterCapabilities,
    AdServerAdapter,
    CreativeEngineAdapter,
    PermissionsReport,
    TargetingCapabilities,
)
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.adapters.springserve.client import SpringServeAuthError, SpringServeClient
from src.adapters.springserve.formats import springserve_creative_formats
from src.adapters.springserve.schemas import (
    SPRINGSERVE_HOSTS,
    SpringServeConnectionConfig,
    SpringServeProductConfig,
)
from src.adapters.springserve.targeting import build_targeting, validate_targeting
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
    UpdateMediaBuyError,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

logger = logging.getLogger(__name__)


class SpringServeAdapter(AdServerAdapter):
    """Adapter for the SpringServe (Magnite) ad-server REST API."""

    adapter_name = "springserve"

    # Video + CTV is SpringServe's home; audio (Magnite x iHeartMedia
    # marketplace) is first-class on the same API surface.
    default_channels = ["olv", "ctv", "streaming_audio", "podcast"]
    default_delivery_measurement = {"provider": "springserve"}

    connection_config_class = SpringServeConnectionConfig
    product_config_class = SpringServeProductConfig
    capabilities = AdapterCapabilities(
        inventory_entity_label="Supply Tags",
        supports_inventory_sync=True,
        supports_inventory_profiles=True,
        supports_reporting_sync=True,
        supports_geo_targeting=True,
        supports_custom_targeting=True,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_dynamic_products=False,
        supports_realtime_reporting=False,
        supports_webhooks=False,
    )

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        """Resolve SpringServe demand-partner mapping and authentication.

        SpringServe identifies the demand side by an integer Demand Partner ID
        (the parent of every Campaign + Demand Tag this adapter creates).
        Dry-run skips client construction so an admin can scaffold the adapter
        before token provisioning is complete.
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        # SpringServe identifies the demand side by a Demand Partner ID.
        self.demand_partner_id = self.principal.get_adapter_id("springserve") or self.config.get(
            "default_demand_partner_id"
        )
        if not self.demand_partner_id and not self.dry_run:
            raise ValueError(
                f"Principal {principal.principal_id} does not have a SpringServe demand_partner_id "
                "and no default_demand_partner_id is configured"
            )
        # Cast to int -- ORM/JSON may give us a string but the SpringServe
        # API expects integers for IDs.
        if self.demand_partner_id is not None:
            self.demand_partner_id = int(self.demand_partner_id)

        self.email = self.config.get("email")
        self.password = self.config.get("password")
        self.api_token = self.config.get("api_token")
        self.environment = self.config.get("environment", "production")
        self.base_url = SPRINGSERVE_HOSTS.get(self.environment, SPRINGSERVE_HOSTS["production"])

        if self.dry_run:
            self.log(
                "Running in dry-run mode -- SpringServe API calls will be simulated",
                dry_run_prefix=False,
            )
            self._client: SpringServeClient | None = None
        else:
            has_password_grant = bool(self.email) and bool(self.password)
            has_token = bool(self.api_token)
            if not has_password_grant and not has_token:
                raise ValueError("SpringServe config requires either (email + password) or api_token")
            self._client = SpringServeClient(
                email=self.email,
                password=self.password,
                api_token=self.api_token,
                base_url=self.base_url,
            )

    # ----- capabilities -----

    def get_supported_pricing_models(self) -> set[str]:
        return {"cpm", "flat_rate"}

    def get_creative_formats(self) -> list[dict[str, Any]]:
        """Return the static set of VAST video + audio formats this adapter supports."""
        return springserve_creative_formats(self.tenant_id)

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        return TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            nielsen_dma=True,
        )

    def check_permissions(self) -> PermissionsReport:
        """Probe every SpringServe endpoint the adapter depends on."""
        report = self._new_permissions_report(
            dry_run_message="Dry-run mode -- no live SpringServe client to probe with."
        )
        if self.dry_run or self._client is None:
            return report

        probes: list[tuple[str, str, str, str, bool, str]] = [
            ("campaigns_read", "Read/create campaigns", "GET", "/campaigns?per_page=1", True, "create_media_buy"),
            (
                "demand_tags_read",
                "Read/create demand tags (the per-package delivery unit)",
                "GET",
                "/demand_tags?per_page=1",
                True,
                "create_media_buy",
            ),
            (
                "videos_read",
                "Read/upload video & audio creatives",
                "GET",
                "/videos?per_page=1",
                True,
                "sync_creatives",
            ),
            (
                "supply_tags_read",
                "Read supply tags (publisher inventory)",
                "GET",
                "/supply_tags?per_page=1",
                True,
                "inventory_sync",
            ),
            (
                "supply_partners_read",
                "Read supply partners",
                "GET",
                "/supply_partners?per_page=1",
                False,
                "inventory_sync",
            ),
            (
                # Reporting API is POST-only; GET returns 404 with our test
                # account, which our probe correctly treats as "endpoint
                # reachable, scope not denied". Stage 4 replaces this entry
                # with a tiny real POST so we can distinguish "scope granted"
                # from "scope denied" via the 401/403 split.
                "report_submit",
                "Submit delivery report jobs (Reporting API)",
                "GET",
                "/report?per_page=1",
                False,
                "delivery_reporting",
            ),
        ]
        try:
            self._walk_permission_probes(report, probes, self._client.probe, auth_error_types=(SpringServeAuthError,))
        except Exception as exc:
            logger.warning("SpringServe permissions probe failed unexpectedly: %s", exc)
            report.error = f"Permissions probe failed: {type(exc).__name__}: {exc}"
        return report

    # ----- helpers -----

    def _product_config_from_package(self, package: MediaPackage) -> dict[str, Any]:
        impl = getattr(package, "implementation_config", None) or {}
        return impl.get("springserve", impl) if isinstance(impl, dict) else {}

    def _campaign_payload(
        self,
        buy_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        return {
            "name": buy_name,
            "demand_partner_id": self.demand_partner_id,
            "start_date": start_time.date().isoformat(),
            "end_date": end_time.date().isoformat(),
        }

    def _demand_tag_payload(
        self,
        package: MediaPackage,
        campaign_id: str | int,
        rate: float,
        rate_type: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        product_config = self._product_config_from_package(package)
        payload: dict[str, Any] = {
            "name": package.name or package.package_id,
            "campaign_id": campaign_id,
            "demand_partner_id": self.demand_partner_id,
            "start_date": start_time.date().isoformat(),
            "end_date": end_time.date().isoformat(),
            "rate": rate,
            "rate_type": rate_type,
            "impression_goal": package.impressions,
            "targeting": build_targeting(package.targeting_overlay, product_config),
            "external_id": package.package_id,
            "active": False,  # Inactive until a creative is bound.
        }
        if product_config.get("priority") is not None:
            payload["priority"] = product_config["priority"]
        return payload

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

        targeting_error = self._validate_targeting_or_error(packages, validate_targeting, adapter_name="SpringServe")
        if targeting_error is not None:
            return targeting_error

        buy_name = self._buy_name(request)

        if self.dry_run:
            campaign_payload = self._campaign_payload(buy_name, start_time, end_time)
            self.log(f"Would call: POST {self.base_url}/campaigns")
            self.log(f"  Campaign: {campaign_payload}")
            for package in packages:
                rate, rate_type = self._resolve_pricing_rate(package, package_pricing_info)
                payload = self._demand_tag_payload(
                    package,
                    campaign_id="<new>",
                    rate=rate,
                    rate_type=rate_type,
                    start_time=start_time,
                    end_time=end_time,
                )
                self.log(f"Would call: POST {self.base_url}/demand_tags")
                self.log(f"  DemandTag: {payload}")
            return self._build_create_success(request, f"springserve_{buy_name}", packages)

        # Live mode lands in Stage 2 -- explicit pending error for now so
        # callers don't get a misleading success on an unwired path.
        return CreateMediaBuyError(
            errors=[
                Error(
                    code="pending_credentials",
                    message=(
                        "SpringServe live-mode create_media_buy lands in Stage 2. "
                        "Run in dry-run mode until Campaign + Demand Tag write paths are wired."
                    ),
                    details=None,
                )
            ]
        )

    def _buy_name(self, request: CreateMediaBuyRequest) -> str:
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
                    f"Would POST {self.base_url}/videos "
                    f"name={asset.get('name')} demand_partner_id={self.demand_partner_id}"
                )
            return [AssetStatus(creative_id=a["creative_id"], status="approved") for a in assets]
        return [AssetStatus(creative_id=a["creative_id"], status="pending") for a in assets]

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        if self.dry_run:
            for li in line_item_ids:
                for ci in platform_creative_ids:
                    self.log(
                        f"Would PATCH .../demand_tags/{li} creative_id={ci} (or vast_url=<asset-url>) and active=true"
                    )
            return [
                {"line_item_id": li, "creative_id": ci, "status": "success"}
                for li in line_item_ids
                for ci in platform_creative_ids
            ]
        return [
            {"line_item_id": li, "creative_id": ci, "status": "pending"}
            for li in line_item_ids
            for ci in platform_creative_ids
        ]

    # ----- status / delivery -----

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        campaign_id = media_buy_id.removeprefix("springserve_")
        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/campaigns/{campaign_id}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Stage-1: dry-run returns simulated numbers; live mode raises until
        the Stage-4 reporting cache lands.

        Stage 4 replaces this with a reader over a local placement_stats
        cache populated by the Reporting API sync job. Until then live mode
        falls through to an empty delivery response (the SpringServe Reporting
        scope may not be granted yet, and we don't want to fabricate zeros).
        """
        if self.dry_run:
            return self._simulated_delivery_response(
                media_buy_id,
                date_range,
                today,
                target_impressions=500_000,
                cpm=15.0,
                completion_rate=0.85,
            )
        return self._empty_delivery_response(media_buy_id, date_range, currency="USD")

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
            campaign_id = media_buy_id.removeprefix("springserve_")
            if action == "pause_media_buy":
                self.log(f"Would PUT .../campaigns/{campaign_id} active=false")
            elif action == "resume_media_buy":
                self.log(f"Would PUT .../campaigns/{campaign_id} active=true")
            elif action in {"pause_package", "resume_package"} and package_id:
                self.log(f"Would PUT demand_tag external_id={package_id} active=<bool>")
            elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget:
                self.log(f"Would PUT demand_tag external_id={package_id} goal={budget}")
            return UpdateMediaBuySuccess(media_buy_id=media_buy_id, affected_packages=[], implementation_date=today)

        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="pending_credentials",
                    message="SpringServe live-mode update_media_buy lands in Stage 2",
                    details=None,
                )
            ]
        )
