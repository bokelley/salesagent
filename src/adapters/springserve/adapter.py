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
from src.adapters.springserve.client import SpringServeAuthError, SpringServeClient, SpringServeError
from src.adapters.springserve.formats import springserve_creative_formats
from src.adapters.springserve.schemas import (
    SPRINGSERVE_HOSTS,
    SpringServeConnectionConfig,
    SpringServeProductConfig,
)
from src.adapters.springserve.targeting import build_demand_tag_targeting, validate_targeting
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

    def _demand_tag_format(self, package: MediaPackage) -> str:
        """Pick the SpringServe ``format`` field (``video`` / ``audio`` /
        ``display``) from the AdCP Package's ``format_ids``.

        SpringServe encodes media type as a string on the demand tag, not
        as a separate field. We discriminate by the ``springserve_audio_*``
        vs ``springserve_video_*`` format id prefixes from our static
        format declarations.
        """
        for fid in package.format_ids or []:
            fmt_id = fid.id if hasattr(fid, "id") else str(fid)
            if fmt_id.startswith("springserve_audio") or "audio" in fmt_id.lower():
                return "audio"
        return "video"

    def _demand_tag_kwargs(
        self,
        package: MediaPackage,
        campaign_id: int,
        rate: float,
        start_time: datetime,
        end_time: datetime,
        po_number: str | None,
    ) -> dict[str, Any]:
        """Build the kwargs for ``client.demand_tags.create()`` from a package.

        Includes targeting via ``build_demand_tag_targeting`` and the
        per-package demand_code so SpringServe stores the AdCP package_id
        as a searchable code on the tag.
        """
        product_config = self._product_config_from_package(package)
        assert self.demand_partner_id is not None  # enforced in __init__ for live mode
        kwargs: dict[str, Any] = {
            "name": package.name or package.package_id,
            "campaign_id": campaign_id,
            "demand_partner_id": int(self.demand_partner_id),
            "start_date": start_time,
            "end_date": end_time,
            "format": self._demand_tag_format(package),
            "rate": rate,
            "rate_currency": self.config.get("rate_currency", "USD"),
            "demand_code": f"{po_number}_{package.package_id}" if po_number else package.package_id,
            "secondary_code": package.package_id,
            "note": (
                f"Package: {package.name or package.package_id}, Impressions: {package.impressions or 0:,}, CPM: {rate}"
            ),
            "is_active": False,  # Inactive until a creative is bound.
        }
        kwargs.update(build_demand_tag_targeting(package.targeting_overlay, product_config))
        return kwargs

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
        rate_currency = self.config.get("rate_currency", "USD")

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/campaigns")
            self.log(f"  Campaign: name={buy_name} demand_partner_id={self.demand_partner_id}")
            for package in packages:
                rate, _ = self._resolve_pricing_rate(package, package_pricing_info)
                kwargs = self._demand_tag_kwargs(
                    package,
                    campaign_id=0,  # placeholder for dry-run
                    rate=rate,
                    start_time=start_time,
                    end_time=end_time,
                    po_number=request.po_number,
                )
                self.log(f"Would call: POST {self.base_url}/demand_tags")
                self.log(f"  DemandTag: {kwargs}")
            return self._build_create_success(request, f"springserve_{buy_name}", packages)

        # Live mode -- Mapping A: AdCP MediaBuy -> SS Campaign, AdCP Package
        # -> SS Demand Tag. Both created paused; the operator (or a later
        # Stage 3 creative-bind call) flips them active.
        assert self._client is not None
        assert self.demand_partner_id is not None
        try:
            campaign = self._client.campaigns.create(
                name=buy_name,
                demand_partner_id=int(self.demand_partner_id),
                is_active=False,
                code=request.po_number,
                secondary_code=f"adcp_{request.po_number}" if request.po_number else None,
                note=(
                    f"AdCP MediaBuy: po_number={request.po_number}, "
                    f"packages={len(packages)}, "
                    f"flight={start_time.date()}..{end_time.date()}"
                ),
                rate_currency=rate_currency,
            )
            for package in packages:
                rate, _ = self._resolve_pricing_rate(package, package_pricing_info)
                kwargs = self._demand_tag_kwargs(
                    package,
                    campaign_id=campaign.id,
                    rate=rate,
                    start_time=start_time,
                    end_time=end_time,
                    po_number=request.po_number,
                )
                self._client.demand_tags.create(**kwargs)
        except SpringServeError as exc:
            logger.warning("SpringServe create_media_buy failed: %s body=%s", exc, exc.body)
            return CreateMediaBuyError(
                errors=[
                    Error(
                        code="upstream_error",
                        message=f"SpringServe rejected the request: {exc}",
                        details=None,
                    )
                ]
            )

        return self._build_create_success(request, f"springserve_{campaign.id}", packages)

    def _buy_name(self, request: CreateMediaBuyRequest) -> str:
        if request.po_number:
            return f"adcp_{request.po_number}"
        return f"adcp_{int(datetime.now(UTC).timestamp())}"

    # ----- creatives -----

    @staticmethod
    def _asset_media_type(asset: dict[str, Any]) -> tuple[str, str]:
        """Return ``(creative_format, creative_content_type)`` for an asset.

        Routing is driven by the AdCP Format id prefix (``springserve_audio_*``
        vs ``springserve_video_*``) when present, falling back to the asset's
        own ``content_type`` hint, then to video/mp4 as a safe default.
        """
        fid = (asset.get("format_id") or {}).get("id") if isinstance(asset.get("format_id"), dict) else None
        is_audio = (isinstance(fid, str) and "audio" in fid.lower()) or str(
            asset.get("content_type", "")
        ).lower().startswith("audio/")
        if is_audio:
            return "audio", str(asset.get("content_type") or "audio/mpeg")
        return "video", str(asset.get("content_type") or "video/mp4")

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """POST each asset to /videos and return AssetStatus with the SS id.

        Audio routing is driven by ``format_id`` -- ``springserve_audio_*``
        format ids produce audio creatives with the matching MIME type.
        AdCP buyers provide a ``url`` or ``media_url`` pointing at the
        hosted asset; SpringServe pulls it during ingest.
        """
        if self.dry_run:
            for asset in assets:
                media_format, content_type = self._asset_media_type(asset)
                self.log(
                    f"Would POST {self.base_url}/videos name={asset.get('name')} "
                    f"format={media_format} content_type={content_type} "
                    f"remote_url={asset.get('url') or asset.get('media_url')}"
                )
            return [AssetStatus(creative_id=a["creative_id"], status="approved") for a in assets]

        assert self._client is not None
        assert self.demand_partner_id is not None
        statuses: list[AssetStatus] = []
        for asset in assets:
            remote_url = asset.get("url") or asset.get("media_url") or asset.get("creative_remote_url")
            if not remote_url:
                logger.warning(
                    "SpringServe asset %s missing remote URL (need 'url' or 'media_url')",
                    asset.get("creative_id"),
                )
                statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                continue
            media_format, content_type = self._asset_media_type(asset)
            try:
                created = self._client.creatives.create(
                    name=asset.get("name") or f"adcp-{asset['creative_id']}",
                    demand_partner_id=int(self.demand_partner_id),
                    creative_remote_url=remote_url,
                    creative_format=media_format,
                    creative_content_type=content_type,
                    duration_seconds=asset.get("duration_seconds"),
                    width=asset.get("width"),
                    height=asset.get("height"),
                    creative_landing_page_url=asset.get("landing_page_url"),
                    secondary_code=asset["creative_id"],
                )
                statuses.append(AssetStatus(creative_id=str(created.id), status="approved"))
            except SpringServeError as exc:
                logger.warning(
                    "SpringServe creative create failed for asset %s: %s",
                    asset.get("creative_id"),
                    exc,
                )
                statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
        return statuses

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Bind SpringServe creatives to demand tags.

        Demand tags carry a single ``creative_id`` (1:1) or a
        ``line_item_ratios`` rotation list. Stage 3 writes only the
        single-creative path; if multiple creative_ids are supplied for
        the same demand tag, the LAST one wins and earlier ones are
        recorded as ``skipped``. The tag is flipped active on a
        successful bind so it can deliver. Rotation via
        ``line_item_ratios`` lands in a later stage.
        """
        if not platform_creative_ids:
            return []
        winner = platform_creative_ids[-1]
        losers = platform_creative_ids[:-1]
        results: list[dict[str, Any]] = []
        for li in line_item_ids:
            results.extend(self._skip_extra_creative_result(li, ci) for ci in losers)
            results.append(self._bind_creative_to_demand_tag(li, winner))
        return results

    def _skip_extra_creative_result(self, line_item_id: str, creative_id: str) -> dict[str, Any]:
        if self.dry_run:
            self.log(f"Would skip extra creative={creative_id} on demand_tag={line_item_id} (only last wins)")
        return {
            "line_item_id": line_item_id,
            "creative_id": creative_id,
            "status": "skipped",
            "message": "Multiple creatives per demand tag -- only the last is wired in Stage 3.",
        }

    def _bind_creative_to_demand_tag(self, line_item_id: str, creative_id: str) -> dict[str, Any]:
        if self.dry_run:
            self.log(f"Would PUT .../demand_tags/{line_item_id} creative_id={creative_id} is_active=true")
            return {"line_item_id": line_item_id, "creative_id": creative_id, "status": "success"}
        assert self._client is not None
        try:
            self._client.demand_tags.update(int(line_item_id), creative_id=int(creative_id), is_active=True)
        except SpringServeError as exc:
            logger.warning("SpringServe bind creative=%s -> demand_tag=%s failed: %s", creative_id, line_item_id, exc)
            return {
                "line_item_id": line_item_id,
                "creative_id": creative_id,
                "status": "failed",
                "message": str(exc),
            }
        return {"line_item_id": line_item_id, "creative_id": creative_id, "status": "success"}

    # ----- status / delivery -----

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        campaign_id = media_buy_id.removeprefix("springserve_")
        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/campaigns/{campaign_id}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        assert self._client is not None
        try:
            campaign = self._client.campaigns.get(int(campaign_id))
        except SpringServeError as exc:
            logger.warning("SpringServe get_campaign(%s) failed: %s", campaign_id, exc)
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")
        # SpringServe carries the on/off state on ``is_active``; map to the
        # AdCP buyer-facing status enum.
        status = "active" if campaign.is_active else "paused"
        return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status)

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

        # Live mode -- map AdCP actions to Campaign / Demand Tag PUTs.
        assert self._client is not None
        campaign_id = media_buy_id.removeprefix("springserve_")
        try:
            if action == "pause_media_buy":
                self._client.campaigns.update(int(campaign_id), is_active=False)
            elif action == "resume_media_buy":
                self._client.campaigns.update(int(campaign_id), is_active=True)
            elif action in {"pause_package", "resume_package"} and package_id:
                dt_id = self._find_demand_tag_id(int(campaign_id), package_id)
                if dt_id is None:
                    return self._package_not_found_error(package_id)
                self._client.demand_tags.update(dt_id, is_active=(action == "resume_package"))
            elif action in {"update_package_budget", "update_package_impressions"} and package_id and budget:
                # Budget on the demand tag is encoded via the ``budgets`` list
                # of nested objects. Surfaced in Stage 4 alongside reporting;
                # rejected here so callers see an honest error instead of a
                # silent no-op.
                return self._unsupported_action_error(f"{action} (pending Stage 4)")
            else:
                return self._unsupported_action_error(action)
        except SpringServeError as exc:
            logger.warning("SpringServe update_media_buy failed: %s body=%s", exc, exc.body)
            return UpdateMediaBuyError(
                errors=[
                    Error(
                        code="upstream_error",
                        message=f"SpringServe rejected the update: {exc}",
                        details=None,
                    )
                ]
            )
        from src.core.schemas import AffectedPackage

        affected = [AffectedPackage(package_id=package_id)] if package_id else []
        return UpdateMediaBuySuccess(
            media_buy_id=media_buy_id,
            affected_packages=affected,
            implementation_date=today,
        )

    def _find_demand_tag_id(self, campaign_id: int, package_id: str) -> int | None:
        """Look up the SpringServe demand_tag.id for an AdCP package_id.

        The demand_tag's ``secondary_code`` is set to the AdCP package_id
        at creation time, so we can find it by scanning the campaign's
        demand_tag_ids. SpringServe's per-campaign demand-tag list isn't a
        free filter on the docs, so we fetch each by id; in practice
        campaigns have at most a handful of demand tags so the round-trip
        cost is low.
        """
        assert self._client is not None
        try:
            campaign = self._client.campaigns.get(campaign_id)
        except SpringServeError:
            return None
        for dt_id in campaign.demand_tag_ids:
            try:
                tag = self._client.demand_tags.get(dt_id)
            except SpringServeError:
                continue
            if tag.secondary_code == package_id:
                return tag.id
        return None

    def _package_not_found_error(self, package_id: str) -> UpdateMediaBuyError:
        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="package_not_found",
                    message=f"No SpringServe demand tag found for package_id={package_id!r}",
                    details=None,
                )
            ]
        )
