"""Shared GAM ReportingService construction for background syncs."""

from __future__ import annotations

from src.adapters.gam import GAMClientManager, build_gam_config_from_adapter
from src.adapters.gam_reporting_service import GAMReportingService
from src.core.database.database_session import get_db_session
from src.core.database.repositories.adapter_config import AdapterConfigRepository


def build_gam_reporting_service_for_tenant(tenant_id: str) -> GAMReportingService:
    """Build a GAM reporting client from the tenant's stored adapter config."""
    with get_db_session() as session:
        adapter_config = AdapterConfigRepository(session, tenant_id).find_by_tenant()
        if (
            adapter_config is None
            or adapter_config.adapter_type != "google_ad_manager"
            or not adapter_config.gam_network_code
        ):
            raise ValueError("GAM not configured")
        gam_config = build_gam_config_from_adapter(adapter_config)
        if "service_account_json" not in gam_config and "refresh_token" not in gam_config:
            raise ValueError("No GAM authentication configured")
        network_code = adapter_config.gam_network_code
        network_timezone = adapter_config.gam_network_timezone

    gam_client = GAMClientManager(gam_config, network_code=network_code).get_client()
    return GAMReportingService(gam_client, network_timezone=network_timezone)
