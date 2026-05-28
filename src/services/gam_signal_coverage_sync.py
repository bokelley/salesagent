"""Persist GAM custom-targeting coverage forecasts onto tenant signals.

The reporting script can answer "what share of price-priority inventory
carried each custom key-value?". This service makes that server-owned:
run the report for mapped GAM key-value signals, store a per-signal
``coverage_forecast`` in ``TenantSignal.adapter_config``, and notify
buyers that ``get_signals`` should be refreshed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from src.adapters.gam_reporting_service import GAMReportingService
from src.core.database.database_session import get_db_session
from src.core.database.models import TenantSignal
from src.core.database.repositories.tenant_signal import TenantSignalRepository
from src.core.signal_ids import adcp_safe_signal_id
from src.services.adapter_sync_orchestration import _sanitize_error_message
from src.services.catalog_sync_helpers import (
    CatalogSyncResult,
    create_running_catalog_sync_job,
    fail_catalog_sync_job,
    finish_catalog_sync_job,
    new_sync_id,
)
from src.services.gam_reporting_sync_helpers import build_gam_reporting_service_for_tenant
from src.services.protocol_change_webhooks import notify_signal_catalog_changes

logger = logging.getLogger(__name__)

KIND_SIGNAL_COVERAGE = "signal_coverage"
DEFAULT_DATE_RANGE = "this_month"
DEFAULT_LINE_ITEM_TYPES = ["PRICE_PRIORITY"]


@dataclass
class SignalCoverageSyncResult(CatalogSyncResult):
    """Summary for one GAM signal coverage sync run."""

    updated_signal_ids: list[str] = field(default_factory=list)


def run_gam_signal_coverage_sync(
    *,
    tenant_id: str,
    triggered_by: str = "manual",
    triggered_by_id: str | None = None,
    date_range: str = DEFAULT_DATE_RANGE,
    line_item_types: list[str] | None = None,
    min_value_impressions: int = 0,
    sync_id: str | None = None,
) -> SignalCoverageSyncResult:
    """Run a GAM signal coverage sync and persist the result.

    This is intentionally separate from ``run_inventory_sync``: it uses GAM
    Reporting API history, not taxonomy discovery. The scheduler can run it
    daily without forcing a full inventory crawl.
    """
    sync_id = sync_id or new_sync_id()
    started_at = datetime.now(UTC)
    effective_line_item_types = list(line_item_types or DEFAULT_LINE_ITEM_TYPES)
    create_running_catalog_sync_job(
        tenant_id=tenant_id,
        sync_id=sync_id,
        sync_type=KIND_SIGNAL_COVERAGE,
        triggered_by=triggered_by,
        triggered_by_id=triggered_by_id,
        started_at=started_at,
        date_range=date_range,
        line_item_types=effective_line_item_types,
    )

    try:
        reporting = _build_reporting_service(tenant_id)
        updated_signal_ids, counts, errors = _sync_key_value_signal_coverage(
            tenant_id=tenant_id,
            reporting=reporting,
            date_range=date_range,
            line_item_types=effective_line_item_types,
            min_value_impressions=min_value_impressions,
        )
        succeeded = not errors
        finished_at = datetime.now(UTC)
        progress = {"counts": counts, "errors": errors, "updated_signal_ids": updated_signal_ids}
        summary = {"updated_signals": len(updated_signal_ids), "keys_queried": counts.get("keys_queried", 0)}
        finish_catalog_sync_job(tenant_id, sync_id, succeeded, counts, errors, summary, progress, finished_at)

        if updated_signal_ids:
            notify_signal_catalog_changes(
                tenant_id=tenant_id,
                action="updated",
                signal_ids=updated_signal_ids,
                data={"changed_fields": ["coverage_forecast"], "sync_id": sync_id},
            )

        return SignalCoverageSyncResult(
            sync_id, tenant_id, started_at, finished_at, succeeded, counts, errors, updated_signal_ids
        )
    except Exception as exc:
        finished_at, error_message = fail_catalog_sync_job(tenant_id=tenant_id, sync_id=sync_id, exc=exc)
        logger.exception("GAM signal coverage sync failed for tenant=%s", tenant_id)
        return SignalCoverageSyncResult(
            sync_id, tenant_id, started_at, finished_at, False, errors={"sync": error_message}
        )


def _build_reporting_service(tenant_id: str) -> GAMReportingService:
    return build_gam_reporting_service_for_tenant(tenant_id)


def _sync_key_value_signal_coverage(
    *,
    tenant_id: str,
    reporting: GAMReportingService,
    date_range: str,
    line_item_types: list[str],
    min_value_impressions: int,
) -> tuple[list[str], dict[str, int], dict[str, str]]:
    with get_db_session() as session:
        session.info["platform_background_worker"] = True
        repo = TenantSignalRepository(session, tenant_id)
        signals_by_key = _custom_key_value_signals_by_key(repo.list_all())
        if not signals_by_key:
            return [], {"signals_seen": 0, "keys_queried": 0, "signals_updated": 0}, {}

        updated_signal_ids: list[str] = []
        errors: dict[str, str] = {}
        sync_time = datetime.now(UTC).isoformat()
        try:
            bulk_coverage = reporting.get_custom_targeting_value_coverage_for_value_ids(
                date_range,  # type: ignore[arg-type]
                value_ids=_mapped_value_ids(signals_by_key),
                values_by_id=_signal_value_metadata(signals_by_key),
                line_item_types=line_item_types,
                min_value_impressions=min_value_impressions,
            )
        except Exception as exc:
            errors["bulk_value_coverage"] = _sanitize_error_message(f"{type(exc).__name__}: {exc}")
        else:
            value_rows_by_id = {
                str(row.get("value_id")): row for row in bulk_coverage.get("values") or [] if row.get("value_id")
            }
            for key_id, signals in signals_by_key.items():
                for signal in signals:
                    key_coverage = _key_coverage_for_signal(
                        signal=signal,
                        key_id=key_id,
                        bulk_coverage=bulk_coverage,
                        value_rows_by_id=value_rows_by_id,
                    )
                    forecast = _coverage_forecast_for_signal(signal, key_coverage)
                    if forecast is None:
                        continue
                    adapter_config = dict(signal.adapter_config or {})
                    adapter_config["coverage_forecast"] = forecast
                    adapter_config["coverage_synced_at"] = sync_time
                    adapter_config["coverage_source"] = {
                        "adapter": "google_ad_manager",
                        "sync_kind": KIND_SIGNAL_COVERAGE,
                        "date_range": key_coverage.get("date_range"),
                        "window_start": key_coverage.get("window_start"),
                        "window_end": key_coverage.get("window_end"),
                        "line_item_types": list(line_item_types),
                    }
                    signal.adapter_config = adapter_config
                    updated_signal_ids.append(signal.signal_id)

        counts = {
            "signals_seen": sum(len(signals) for signals in signals_by_key.values()),
            "keys_queried": len(signals_by_key),
            "value_ids_queried": len(_mapped_value_ids(signals_by_key)),
            "signals_updated": len(updated_signal_ids),
            "keys_failed": len(errors),
            "report_value_chunk_count": int(
                ((bulk_coverage.get("coverage") or {}).get("report_value_chunk_count") if not errors else 0) or 0
            ),
        }
        session.commit()
        return updated_signal_ids, counts, errors


def _mapped_value_ids(signals_by_key: dict[str, list[TenantSignal]]) -> list[str]:
    value_ids: set[str] = set()
    for signals in signals_by_key.values():
        for signal in signals:
            value_id = (signal.adapter_config or {}).get("value_id")
            if value_id:
                value_ids.add(str(value_id))
    return sorted(value_ids)


def _signal_value_metadata(signals_by_key: dict[str, list[TenantSignal]]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for signals in signals_by_key.values():
        for signal in signals:
            cfg = signal.adapter_config or {}
            value_id = cfg.get("value_id")
            if not value_id:
                continue
            metadata[str(value_id)] = {
                "id": str(value_id),
                "name": _signal_value_label(signal),
                "display_name": _signal_value_label(signal),
            }
    return metadata


def _key_coverage_for_signal(
    *,
    signal: TenantSignal,
    key_id: str,
    bulk_coverage: dict[str, Any],
    value_rows_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    cfg = signal.adapter_config or {}
    value_id = str(cfg.get("value_id") or "")
    value_row = value_rows_by_id.get(value_id)
    if value_row is None and value_id:
        value_row = {
            "value_id": value_id,
            "value": _signal_value_label(signal),
            "display_name": _signal_value_label(signal),
            "impressions": 0,
            "revenue": 0.0,
            "share_of_inventory": 0.0,
        }
    key_name = cfg.get("key_name") or _signal_key_name(signal) or str(key_id)
    return {
        "date_range": bulk_coverage.get("date_range"),
        "window_start": bulk_coverage.get("window_start"),
        "window_end": bulk_coverage.get("window_end"),
        "timezone": bulk_coverage.get("timezone"),
        "data_valid_until": bulk_coverage.get("data_valid_until"),
        "key": {"id": str(key_id), "name": key_name},
        "filters": bulk_coverage.get("filters") or {},
        "total_inventory": bulk_coverage.get("total_inventory") or {},
        "values": [value_row] if value_row else [],
    }


def _signal_key_name(signal: TenantSignal) -> str | None:
    if "=" not in signal.name:
        return None
    key_name = signal.name.split("=", 1)[0].strip()
    return key_name or None


def _signal_value_label(signal: TenantSignal) -> str:
    if "=" in signal.name:
        label = signal.name.split("=", 1)[1].strip()
        if label:
            return label
    return signal.name


def _custom_key_value_signals_by_key(signals: list[TenantSignal]) -> dict[str, list[TenantSignal]]:
    by_key: dict[str, list[TenantSignal]] = {}
    for signal in signals:
        cfg = signal.adapter_config or {}
        if cfg.get("kind") != "custom_key_value":
            continue
        key_id = cfg.get("key_id")
        value_id = cfg.get("value_id")
        if not key_id or not value_id:
            continue
        by_key.setdefault(str(key_id), []).append(signal)
    return by_key


def _coverage_forecast_for_signal(signal: TenantSignal, key_coverage: dict[str, Any]) -> dict[str, Any] | None:
    cfg = signal.adapter_config or {}
    value_id = str(cfg.get("value_id") or "")
    if not value_id:
        return None

    value_rows = key_coverage.get("values") or []
    value_row = next((row for row in value_rows if str(row.get("value_id")) == value_id), None)
    total_inventory = key_coverage.get("total_inventory") or {}
    total_impressions = int(total_inventory.get("impressions") or 0)
    present_impressions = int((value_row or {}).get("impressions") or 0)
    absent_impressions = max(0, total_impressions - present_impressions)
    present_revenue = float((value_row or {}).get("revenue") or 0.0)
    signal_value = (value_row or {}).get("value") or value_id
    signal_value_name = (value_row or {}).get("display_name") or signal_value
    window_start = str(key_coverage.get("window_start") or "")
    window_end = str(key_coverage.get("window_end") or "")
    key = key_coverage.get("key") or {}
    line_item_types = (key_coverage.get("filters") or {}).get("line_item_types") or DEFAULT_LINE_ITEM_TYPES
    generated_at = datetime.now(UTC)
    valid_until = key_coverage.get("data_valid_until") or (generated_at + timedelta(hours=6)).isoformat()
    wire_signal_id = adcp_safe_signal_id(signal.signal_id)

    forecast = {
        "forecast_range_unit": "availability",
        "method": "estimate",
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until,
        "scope": {
            "kind": "inventory",
            "label": f"{', '.join(line_item_types)} inventory",
            "line_item_types": line_item_types,
            "date_range": {"start": window_start, "end": window_end},
            "ad_server": "google_ad_manager",
            "custom_targeting_key_id": str(key.get("id") or cfg.get("key_id") or ""),
            "custom_targeting_key_name": key.get("name"),
            "custom_targeting_value_id": value_id,
        },
        "bucket_semantics": "exclusive",
        "bucket_completeness": "partial",
        "points": [
            {
                "label": str(signal_value_name),
                "dimensions": [
                    {
                        "kind": "signal",
                        "signal_id": wire_signal_id,
                        "signal_name": signal.name,
                        "signal_value": signal_value,
                        "signal_value_name": signal_value_name,
                        "presence": "present",
                    }
                ],
                "metrics": {
                    "impressions": {"mid": present_impressions},
                    "spend": {"mid": round(present_revenue, 2)},
                    "coverage_rate": {"mid": _coverage_rate(present_impressions, total_impressions)},
                },
            },
            {
                "label": "not present",
                "dimensions": [
                    {
                        "kind": "signal",
                        "signal_id": wire_signal_id,
                        "signal_name": signal.name,
                        "signal_value": None,
                        "presence": "absent",
                    }
                ],
                "metrics": {
                    "impressions": {"mid": absent_impressions},
                    "coverage_rate": {"mid": _coverage_rate(absent_impressions, total_impressions)},
                },
            },
        ],
        "ext": {
            "total_inventory": total_inventory,
            "source_coverage": {
                "date_range": key_coverage.get("date_range"),
                "window_start": window_start,
                "window_end": window_end,
                "timezone": key_coverage.get("timezone"),
            },
        },
    }
    _validate_coverage_forecast(forecast)
    return forecast


def _coverage_rate(impressions: int, total_impressions: int) -> float:
    if total_impressions <= 0:
        return 0.0
    return round(impressions / total_impressions, 6)


def _validate_coverage_forecast(forecast: dict[str, Any]) -> None:
    from adcp.types.generated_poc.core.signal_coverage_forecast import SignalCoverageForecast

    try:
        SignalCoverageForecast.model_validate(forecast)
    except ValidationError as exc:
        raise ValueError(f"Generated invalid SignalCoverageForecast: {exc}") from exc
