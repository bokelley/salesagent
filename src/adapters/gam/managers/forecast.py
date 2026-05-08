"""GAM Availability Forecast Manager.

Wraps GAM ``ForecastService.getAvailabilityForecast`` and translates the
result into the AdCP-spec ``DeliveryForecast`` shape that
``products.forecast`` accepts.

**Hard rule: AdCP spec compliance is non-negotiable.** Per the user's
instruction at the top of this implementation session: "deviation from
the spec is not tolerated and will result in the whole project failing."
Therefore:

- This manager NEVER returns a ``forecast`` payload that fails
  ``adcp.types.DeliveryForecast`` validation. On any error (missing
  targeting, GAM rejection, ``NO_FORECAST_YET``, network issue, null
  ``availableUnits``), the spec field is ``None`` and the diagnostic
  details are returned alongside in a separate ``error`` field for the
  operator UI.
- The successful payload conforms to the AdCP ``DeliveryForecast``:
  ``points`` (≥1 entry), ``method`` (one of estimate/modeled/guaranteed),
  ``currency`` (string). GAM availability forecasts are mapped to
  ``method='estimate'`` since GAM does not guarantee delivery from
  ``getAvailabilityForecast``.
- The caller (``products.refresh_product_forecast`` admin endpoint)
  persists ``product.forecast = result.forecast`` — which is either a
  spec-compliant ``DeliveryForecast`` dict or ``None``. Never a
  half-shaped diagnostic blob.

GAM API reference:
- https://developers.google.com/ad-manager/api/reference/v202602/ForecastService
- https://developers.google.com/ad-manager/api/reference/v202602/ForecastService.getAvailabilityForecast

AdCP spec reference: ``adcp.types.generated_poc.core.delivery_forecast.DeliveryForecast``
(see also https://docs.adcontextprotocol.org/docs/protocol/required-tasks).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForecastResult:
    """Outcome of a single forecast call.

    ``forecast`` is a spec-compliant AdCP ``DeliveryForecast`` dict (suitable
    for direct persist into ``products.forecast``) or ``None`` if no
    forecast could be produced. ``error`` carries the human-readable
    diagnostic (used by the admin UI; not persisted as part of the spec
    field). ``window_start`` / ``window_end`` echo the requested forecast
    window. ``fetched_at`` is the call timestamp.
    """

    forecast: dict | None
    error: str | None
    window_start: str
    window_end: str
    fetched_at: str

    def to_dict(self) -> dict:
        """Serialise to the wire shape returned by the admin endpoint."""
        return {
            "forecast": self.forecast,
            "error": self.error,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "fetched_at": self.fetched_at,
        }


def _to_gam_datetime(d: date, time_zone_id: str = "America/New_York") -> dict:
    """Convert a Python ``date`` to a GAM ``DateTime`` dict at noon
    publisher timezone.

    Noon avoids ``START_DATE_TIME_IS_IN_PAST`` rejections when today is
    passed as the start — midnight has already passed by the time the
    SOAP call lands.
    """
    return {
        "date": {"year": d.year, "month": d.month, "day": d.day},
        "hour": 12,
        "minute": 0,
        "second": 0,
        "timeZoneId": time_zone_id,
    }


def _build_spec_compliant_forecast(
    available_units: int,
    *,
    label: str,
    currency: str,
) -> dict:
    """Build an AdCP-spec ``DeliveryForecast`` dict from a single GAM
    availability number.

    Schema: ``method='estimate'``, ``currency`` from caller (typically the
    product's currency or tenant default), ``points`` with one entry whose
    ``metrics.impressions.mid`` carries the GAM number. ``low``/``high``
    are intentionally omitted because GAM does not return a confidence
    interval from ``getAvailabilityForecast``; the spec marks all three as
    optional.
    """
    return {
        "method": "estimate",
        "currency": currency,
        "points": [
            {
                "label": label,
                "metrics": {
                    "impressions": {"mid": float(available_units)},
                },
            }
        ],
    }


class GAMForecastManager:
    """Resolves a product's targeting + flight into a synthetic
    ``ProspectiveLineItem`` and asks GAM for an availability forecast.

    Constructed per-tenant (carries the tenant's ``GAMClientManager`` and
    optional advertiser_id). The public surface is just ``get_for_product``
    — everything else is internal.
    """

    def __init__(self, client_manager: Any, advertiser_id: str | None = None) -> None:
        self.client_manager = client_manager
        self.advertiser_id = advertiser_id
        self._service = None

    def _get_service(self):
        """Lazily resolve and cache the ``ForecastService`` proxy."""
        if self._service is None:
            self._service = self.client_manager.get_service("ForecastService")
        return self._service

    def get_for_product(
        self,
        product: Any,
        *,
        days: int = 7,
        time_zone_id: str = "America/New_York",
        currency: str = "USD",
    ) -> ForecastResult:
        """Run an availability forecast for ``product`` over the next
        ``days`` days starting tomorrow. Returns a ``ForecastResult``.

        Per the design contract at the top of this module: NEVER raises.
        All failures land in ``ForecastResult.error`` with
        ``ForecastResult.forecast == None``.
        """
        fetched_at = datetime.now(UTC).isoformat()
        start_d = date.today() + timedelta(days=1)
        end_d = start_d + timedelta(days=days)
        window_start = start_d.isoformat()
        window_end = end_d.isoformat()

        impl_config = getattr(product, "implementation_config", None) or {}
        ad_unit_ids = impl_config.get("targeted_ad_unit_ids") or []
        line_item_type = impl_config.get("line_item_type") or "STANDARD"
        cost_type = impl_config.get("cost_type") or "CPM"

        if not ad_unit_ids:
            return ForecastResult(
                forecast=None,
                error=(
                    "Product has no targeted_ad_unit_ids in implementation_config; "
                    "GAM forecasting requires at least one ad unit to target."
                ),
                window_start=window_start,
                window_end=window_end,
                fetched_at=fetched_at,
            )

        line_item: dict[str, Any] = {
            "lineItemType": line_item_type,
            "costType": cost_type,
            "startDateTime": _to_gam_datetime(start_d, time_zone_id),
            "endDateTime": _to_gam_datetime(end_d, time_zone_id),
            "targeting": {
                "inventoryTargeting": {
                    "targetedAdUnits": [
                        {
                            "adUnitId": str(aid),
                            "includeDescendants": bool(impl_config.get("include_descendants", True)),
                        }
                        for aid in ad_unit_ids
                    ],
                },
            },
            "primaryGoal": {
                "goalType": "LIFETIME",
                "unitType": "IMPRESSIONS",
                "units": 1_000_000,
            },
        }
        # advertiserId is on ProspectiveLineItem (top level), NOT inside
        # LineItem in v202602 WSDL — verified via zeep introspection. Putting
        # it inside line_item triggers KeyError: 'advertiserId' from the
        # googleads SOAP serializer.
        prospective: dict[str, Any] = {"lineItem": line_item}
        if self.advertiser_id:
            prospective["advertiserId"] = str(self.advertiser_id)

        try:
            service = self._get_service()
            result = service.getAvailabilityForecast(
                prospective,
                {
                    "includeTargetingCriteriaBreakdown": False,
                    "includeContendingLineItems": False,
                },
            )

            raw_available = getattr(result, "availableUnits", None)
            if raw_available is None:
                return ForecastResult(
                    forecast=None,
                    error=("GAM returned null availableUnits (empty forecast window or misconfigured targeting)"),
                    window_start=window_start,
                    window_end=window_end,
                    fetched_at=fetched_at,
                )

            available_units = int(raw_available)
            spec_forecast = _build_spec_compliant_forecast(
                available_units,
                label=f"GAM availability {window_start} to {window_end}",
                currency=currency,
            )
            return ForecastResult(
                forecast=spec_forecast,
                error=None,
                window_start=window_start,
                window_end=window_end,
                fetched_at=fetched_at,
            )

        except Exception as exc:
            logger.warning(
                "GAM forecast failed for product %s (ad_unit_ids=%s, line_item_type=%s): %s",
                getattr(product, "product_id", "<unknown>"),
                ad_unit_ids,
                line_item_type,
                exc,
            )
            return ForecastResult(
                forecast=None,
                error=str(exc),
                window_start=window_start,
                window_end=window_end,
                fetched_at=fetched_at,
            )

    def get_available_units(
        self,
        ad_unit_ids: list[str],
        start_date: date,
        end_date: date,
        *,
        line_item_type: str = "STANDARD",
        cost_type: str = "CPM",
        include_descendants: bool = True,
        time_zone_id: str = "America/New_York",
    ) -> int | None:
        """Return GAM's available-impressions forecast for an explicit
        ad_unit_ids + flight window.

        Convenience wrapper for callers (e.g. the create_media_buy
        pre-flight overbook gate) that have the targeting and dates from
        the buyer's request, not from a Product. Returns ``None`` on any
        error so the caller can fail-open without a try/except. The
        full-fat ``get_for_product`` path is still the right shape when
        we want the spec-compliant ``DeliveryForecast`` envelope back
        for product persistence.
        """
        if not ad_unit_ids:
            return None

        line_item: dict[str, Any] = {
            "lineItemType": line_item_type,
            "costType": cost_type,
            "startDateTime": _to_gam_datetime(start_date, time_zone_id),
            "endDateTime": _to_gam_datetime(end_date, time_zone_id),
            "targeting": {
                "inventoryTargeting": {
                    "targetedAdUnits": [
                        {"adUnitId": str(aid), "includeDescendants": include_descendants} for aid in ad_unit_ids
                    ],
                },
            },
            "primaryGoal": {
                "goalType": "LIFETIME",
                "unitType": "IMPRESSIONS",
                "units": 1_000_000,
            },
        }
        prospective: dict[str, Any] = {"lineItem": line_item}
        if self.advertiser_id:
            prospective["advertiserId"] = str(self.advertiser_id)

        try:
            service = self._get_service()
            result = service.getAvailabilityForecast(
                prospective,
                {
                    "includeTargetingCriteriaBreakdown": False,
                    "includeContendingLineItems": False,
                },
            )
            raw = getattr(result, "availableUnits", None)
            return int(raw) if raw is not None else None
        except Exception as exc:
            logger.warning(
                "GAM forecast failed for ad_unit_ids=%s window=%s..%s: %s",
                ad_unit_ids,
                start_date.isoformat(),
                end_date.isoformat(),
                exc,
            )
            return None
