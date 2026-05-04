"""Adapter connection probe used by the Tenant Management API.

A narrow wrapper that translates the per-adapter health-check API into the
``(success, error)`` tuple the Tenant Management API needs. Heavyweight
permission checks are out of scope here — we just verify that the configured
credentials authenticate.

Tests can monkeypatch :func:`test_adapter_connection` to bypass real API calls.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def test_adapter_connection(adapter_type: str, config: dict[str, Any]) -> tuple[bool, str | None]:
    """Probe the adapter's authentication path.

    Args:
        adapter_type: One of ``"google_ad_manager"`` or ``"mock"``.
        config: Adapter-specific configuration. For GAM this includes
            ``network_code`` and one of ``service_account_json`` /
            ``refresh_token``.

    Returns:
        A ``(success, error)`` tuple. ``error`` is None on success and a
        human-readable string on failure.
    """
    if adapter_type == "mock":
        return True, None

    if adapter_type == "google_ad_manager":
        return _test_gam(config)

    return False, f"Unsupported adapter_type: {adapter_type!r}"


def _test_gam(config: dict[str, Any]) -> tuple[bool, str | None]:
    """Authentication probe for Google Ad Manager."""
    network_code = config.get("network_code")
    if not network_code:
        return False, "GAM network_code is required"

    try:
        # Local import: keeps googleads off the import path for non-GAM tests.
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.utils.health_check import HealthStatus
    except Exception as exc:  # pragma: no cover - import-time failures are environmental
        logger.exception("GAM imports failed")
        return False, f"GAM client unavailable: {exc}"

    try:
        manager = GAMClientManager(config=config, network_code=str(network_code))
        result = manager.test_connection()
    except Exception as exc:
        logger.warning("GAM test_connection raised: %s", exc)
        return False, f"GAM connection probe failed: {exc}"

    if result.status == HealthStatus.HEALTHY:
        return True, None
    return False, result.message or "GAM connection probe returned non-healthy status"
