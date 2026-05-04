"""Adapter connection probe used by the Tenant Management API.

A narrow wrapper that translates the per-adapter health-check API into the
``(success, error)`` tuple the Tenant Management API needs. Heavyweight
permission checks are out of scope here — we just verify that the configured
credentials authenticate.

Tests can monkeypatch :func:`test_adapter_connection` or
:func:`preview_adapter` to bypass real API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AdapterPreview:
    """Metadata returned by :func:`preview_adapter`.

    Used by the Storefront UI to confirm an adapter grant + auto-fill
    currency/timezone before committing to a tenant. ``ok=False`` is a normal
    flow (bad creds) — callers render this inline; the endpoint does NOT
    return 4xx for that case.
    """

    ok: bool
    network_name: str | None = None
    network_code: str | None = None
    currency_code: str | None = None
    time_zone: str | None = None
    inventory_reachable: bool = False
    error: str | None = None


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


def preview_adapter(adapter_type: str, config: dict[str, Any]) -> AdapterPreview:
    """Probe the adapter and return network metadata for Storefront preview.

    On bad creds returns ``AdapterPreview(ok=False, error=...)`` rather than
    raising — the endpoint surfaces this as 200 so the UI can render inline.
    """
    if adapter_type == "mock":
        return AdapterPreview(
            ok=True,
            network_name="Mock Network",
            network_code=str(config.get("network_code") or "mock-network"),
            currency_code="USD",
            time_zone="UTC",
            inventory_reachable=True,
        )

    if adapter_type == "google_ad_manager":
        return _preview_gam(config)

    return AdapterPreview(ok=False, error=f"Unsupported adapter_type: {adapter_type!r}")


def _preview_gam(config: dict[str, Any]) -> AdapterPreview:
    """GAM preview: connection test + ``getCurrentNetwork()`` metadata."""
    network_code = config.get("network_code")
    if not network_code:
        return AdapterPreview(ok=False, error="GAM network_code is required")

    try:
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.utils.health_check import HealthStatus
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("GAM imports failed")
        return AdapterPreview(ok=False, error=f"GAM client unavailable: {exc}")

    try:
        manager = GAMClientManager(config=config, network_code=str(network_code))
        result = manager.test_connection()
    except Exception as exc:
        logger.warning("GAM test_connection raised: %s", exc)
        return AdapterPreview(ok=False, error=f"GAM connection probe failed: {exc}")

    if result.status != HealthStatus.HEALTHY:
        return AdapterPreview(
            ok=False,
            error=result.message or "GAM connection probe returned non-healthy status",
        )

    # Fetch network metadata via getCurrentNetwork(). One extra call after auth proven.
    try:
        client = manager.get_client()
        network = client.GetService("NetworkService").getCurrentNetwork()
    except Exception as exc:
        # Connection works but metadata fetch failed — still ok=true with sparse fields.
        logger.warning("GAM getCurrentNetwork() failed after auth ok: %s", exc)
        return AdapterPreview(
            ok=True,
            network_code=str(network_code),
            inventory_reachable=False,
            error=f"network metadata unavailable: {exc}",
        )

    return AdapterPreview(
        ok=True,
        network_name=getattr(network, "displayName", None),
        network_code=str(getattr(network, "networkCode", network_code)),
        currency_code=getattr(network, "currencyCode", None),
        time_zone=getattr(network, "timeZone", None),
        inventory_reachable=True,
    )
