"""Compatibility imports for the original reporting scheduler module."""

from src.services.adapter_sync_scheduler import (
    REPORTING_INTERVAL_SECONDS as SLEEP_INTERVAL_SECONDS,
)
from src.services.adapter_sync_scheduler import (
    AdapterReportingSyncScheduler,
    _list_eligible_tenants,
    get_adapter_reporting_sync_scheduler,
    start_adapter_reporting_sync_scheduler,
    stop_adapter_reporting_sync_scheduler,
)

__all__ = [
    "AdapterReportingSyncScheduler",
    "SLEEP_INTERVAL_SECONDS",
    "_list_eligible_tenants",
    "get_adapter_reporting_sync_scheduler",
    "start_adapter_reporting_sync_scheduler",
    "stop_adapter_reporting_sync_scheduler",
]
