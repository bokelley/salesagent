"""Patches to ``get_adcp_capabilities`` for spec compliance.

Two amendments stack onto the SDK's default capabilities response:

1. **Envelope ``status`` field** (AdCP 3.0.11). The protocol envelope
   schema requires a top-level ``status`` on every response. The SDK
   emits the body without one; buyer tooling that walks
   ``protocol-envelope.json`` rejects the response and the storyboard
   ``v3_envelope_integrity / no_legacy_status_fields`` step fails.

2. **``portfolio.publisher_domains``** (AdCP 3.x). v3 retired
   ``list_authorized_properties`` and moved the publisher portfolio
   onto ``get_adcp_capabilities``. Populate it per-tenant from the
   ``PublisherPartner`` table so authenticated and unauthenticated
   buyers both see the agent's inventory partners on discovery.
   Sorted alphabetically per CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01.
   Omitted when the tenant has zero partners (the schema's
   ``min_length=1`` on ``Portfolio.publisher_domains`` requires it).

3. **``webhook_signing``** (AdCP 3.x). The SDK exposes a native
   capability block for RFC 9421 webhook signing, but the data is
   tenant-specific: only tenants with an active, locally usable
   ``TenantSigningCredential`` can safely advertise it.

Importing this module monkey-patches
:meth:`adcp.decisioning.handler.PlatformHandler.get_adcp_capabilities`.
Remove the ``status`` shim once the upstream SDK adds envelope status
itself; the portfolio block becomes the SDK's responsibility only when
a future SDK release exposes a per-tenant portfolio hook.
"""

from __future__ import annotations

import logging
from typing import Any

from adcp.decisioning.handler import PlatformHandler
from adcp.server.tenant_router import current_tenant

logger = logging.getLogger(__name__)

_ORIGINAL = PlatformHandler.get_adcp_capabilities

_WEBHOOK_SIGNING_PROFILE = "adcp/webhook-signing/v1"


def _webhook_signing_unsupported() -> dict[str, Any]:
    return {"supported": False, "legacy_hmac_fallback": True}


def _publisher_domains_for_current_tenant() -> list[str]:
    """Return sorted publisher domains for the tenant on the current request.

    Returns an empty list when no tenant is resolved (e.g., the request
    didn't traverse ``SubdomainTenantMiddleware``) or the tenant has no
    ``PublisherPartner`` rows. Failures inside the DB read are swallowed
    with a warning — discovery should never 500 on an inventory-table
    hiccup.
    """
    tenant = current_tenant()
    if tenant is None or not getattr(tenant, "id", None):
        return []
    # Import lazily so this module is import-safe at module-load time
    # (the patch is applied via side-effect import from core.main).
    from src.core.database.repositories.uow import TenantConfigUoW

    try:
        with TenantConfigUoW(tenant.id) as uow:
            assert uow.tenant_config is not None
            return uow.tenant_config.list_publisher_domains()
    except Exception:
        logger.warning(
            "publisher_domains lookup failed for tenant %r; emitting empty portfolio",
            tenant.id,
            exc_info=True,
        )
        return []


def _webhook_signing_for_current_tenant() -> dict[str, Any]:
    """Return the tenant-specific AdCP webhook-signing capability block."""
    tenant = current_tenant()
    if tenant is None or not getattr(tenant, "id", None):
        return _webhook_signing_unsupported()

    from src.services.webhook_signing import (
        SIGNING_MODE_RFC9421,
        SigningConfigurationError,
        load_active_signing_credential,
    )

    try:
        snapshot = load_active_signing_credential(tenant_id=tenant.id, signing_mode=SIGNING_MODE_RFC9421)
        if snapshot is None:
            return _webhook_signing_unsupported()
    except SigningConfigurationError:
        logger.warning(
            "webhook signing credential for tenant %r is active but not usable; advertising unsupported",
            tenant.id,
            exc_info=True,
        )
        return _webhook_signing_unsupported()
    except Exception:
        logger.warning(
            "webhook signing capability lookup failed for tenant %r; advertising unsupported",
            tenant.id,
            exc_info=True,
        )
        return _webhook_signing_unsupported()

    return {
        "supported": True,
        "profile": _WEBHOOK_SIGNING_PROFILE,
        "algorithms": [snapshot.alg],
        "legacy_hmac_fallback": True,
    }


async def _get_adcp_capabilities_patched(
    self: PlatformHandler,
    params: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    result = await _ORIGINAL(self, params, context)
    if not isinstance(result, dict):
        return result

    if "status" not in result:
        result["status"] = "completed"

    domains = _publisher_domains_for_current_tenant()
    if domains:
        portfolio = result.setdefault("portfolio", {})
        if isinstance(portfolio, dict):
            portfolio["publisher_domains"] = domains

    result["webhook_signing"] = _webhook_signing_for_current_tenant()

    return result


PlatformHandler.get_adcp_capabilities = _get_adcp_capabilities_patched  # type: ignore[method-assign]
