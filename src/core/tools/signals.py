"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
import time
import uuid

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError

logger = logging.getLogger(__name__)

from adcp.types.generated_poc.core.vendor_pricing_option import VendorPricingOption
from adcp.types.generated_poc.signals.get_signals_response import Range

from src.core.auth import get_principal_object
from src.core.database.models import TenantSignal
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    ActivateSignalResponse,
    GetSignalsRequest,
    GetSignalsResponse,
    Signal,
    SignalDeployment,
)
from src.core.testing_hooks import AdCPTestContext


def _cpm_pricing_option(cpm: float, currency: str = "USD") -> list[VendorPricingOption]:
    """Build a single-element pricing_options list for a CPM signal.

    adcp 4.4.3 unified signal pricing onto the shared VendorPricingOption
    discriminated union (model='cpm' is VendorPricingOption7 = cpm + pricing_option_id).
    """
    return [
        VendorPricingOption.model_validate(
            {"pricing_option_id": f"cpm_{currency.lower()}", "model": "cpm", "cpm": cpm, "currency": currency}
        )
    ]


def _tenant_signal_to_adcp(
    ts: TenantSignal,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> Signal:
    """Translate an operator-authored ``TenantSignal`` row to the AdCP ``Signal``
    wire shape.

    ``adapter_config`` is intentionally elided — operator-authored data, not
    for storefront consumption. The storefront uses ``value_type`` /
    ``categories`` / ``range`` to render UI; activation (and any adapter-side
    resolution) happens through ``activate_signal`` / ``create_media_buy``.
    """
    range_obj: Range | None = None
    if ts.range_min is not None or ts.range_max is not None:
        range_obj = Range(min=ts.range_min, max=ts.range_max)

    # AdCP's ``signal_id.id`` restricts characters to ``[a-zA-Z0-9_-]+``.
    # Operators tend to want hierarchical identifiers like
    # ``audience.sports_fans``; sanitize ``.`` to ``_`` for the wire and use
    # the same shape for ``signal_agent_segment_id`` so a storefront round-
    # trips the same identifier through activation.
    wire_id = ts.signal_id.replace(".", "_")

    # AdCP validates ``signal_id.agent_url`` as a URL; the sample signals
    # use the public salesagent host. Fall back to the same when the tenant
    # hasn't set ``public_agent_url`` so projection doesn't fail validation.
    resolved_agent_url = agent_url or "https://salesagent.adcontextprotocol.org/signals"

    signal_kwargs: dict = {
        "signal_id": {
            "source": "agent",
            "agent_url": resolved_agent_url,
            "id": wire_id,
        },
        "signal_agent_segment_id": wire_id,
        "name": ts.name,
        "description": ts.description or "",
        # Operator-declared signals are the publisher's first-party data
        # by default. Distinguishing marketplace / custom variants would
        # warrant a column on TenantSignal — keep the default simple.
        "signal_type": "owned",
        "data_provider": ts.data_provider or "publisher",
        # No coverage measurement yet — declare 100% (signal applies to
        # any inventory the operator says it applies to) until we wire
        # up coverage stats.
        "coverage_percentage": 100.0,
        "deployments": [
            SignalDeployment(
                platform=ad_server or "mock",
                is_live=True,
                type="platform",
            )
        ],
        # Publisher's own signals are zero-cost on the publisher's own
        # inventory. Operators can layer paid signals via the signals-agent
        # path when those land.
        "pricing_options": _cpm_pricing_option(0.0),
    }
    if ts.value_type:
        signal_kwargs["value_type"] = ts.value_type
    if ts.categories:
        signal_kwargs["categories"] = list(ts.categories)
    if range_obj is not None:
        signal_kwargs["range"] = range_obj
    return Signal.model_validate(signal_kwargs)


def _load_tenant_signals(
    tenant_id: str,
    *,
    ad_server: str | None,
    agent_url: str | None,
) -> list[Signal]:
    """Read operator-authored ``TenantSignal`` rows for the tenant and project
    them onto the AdCP ``Signal`` shape.
    """
    from src.core.database.repositories.uow import TenantSignalUoW

    with TenantSignalUoW(tenant_id) as uow:
        assert uow.tenant_signals is not None
        rows = uow.tenant_signals.list_all()
        return [_tenant_signal_to_adcp(ts, ad_server=ad_server, agent_url=agent_url) for ts in rows]


async def _get_signals_impl(req: GetSignalsRequest, identity: ResolvedIdentity | None = None) -> GetSignalsResponse:
    """Shared implementation for get_signals (used by both MCP and A2A).

    Args:
        req: Request containing query parameters for signal discovery
        identity: Resolved identity from transport boundary

    Returns:
        GetSignalsResponse with matching signals
    """
    # Principal ID available via identity.principal_id if needed
    _ = identity.principal_id if identity else None

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    assert identity is not None, "identity is required for signals"
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Mock implementation - in production, this would query from a signal provider
    # or the ad server's available audience segments
    signals = []

    # Sample signals for demonstration using local types (extend AdCP library types)
    sample_signals = [
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "auto_intenders_q1_2025",
            },
            signal_agent_segment_id="auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Users actively researching new vehicles in Q1 2025",
            signal_type="marketplace",
            data_provider="Acme Data Solutions",
            coverage_percentage=85.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(3.0),
        ),
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "luxury_travel_enthusiasts",
            },
            signal_agent_segment_id="luxury_travel_enthusiasts",
            name="Luxury Travel Enthusiasts",
            description="High-income individuals interested in premium travel experiences",
            signal_type="marketplace",
            data_provider="Premium Audience Co",
            coverage_percentage=75.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(5.0),
        ),
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "sports_content",
            },
            signal_agent_segment_id="sports_content",
            name="Sports Content Pages",
            description="Target ads on sports-related content",
            signal_type="owned",
            data_provider="Publisher Sports Network",
            coverage_percentage=95.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(1.5),
        ),
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "finance_content",
            },
            signal_agent_segment_id="finance_content",
            name="Finance & Business Content",
            description="Target ads on finance and business content",
            signal_type="owned",
            data_provider="Financial News Corp",
            coverage_percentage=88.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(2.0),
        ),
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "urban_millennials",
            },
            signal_agent_segment_id="urban_millennials",
            name="Urban Millennials",
            description="Millennials living in major metropolitan areas",
            signal_type="marketplace",
            data_provider="Demographics Plus",
            coverage_percentage=78.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(1.8),
        ),
        Signal(
            signal_id={
                "source": "agent",
                "agent_url": "https://salesagent.adcontextprotocol.org/signals",
                "id": "pet_owners",
            },
            signal_agent_segment_id="pet_owners",
            name="Pet Owners",
            description="Households with dogs or cats",
            signal_type="marketplace",
            data_provider="Lifestyle Data Inc",
            coverage_percentage=92.0,
            deployments=[SignalDeployment(platform="google_ad_manager", is_live=True, type="platform")],
            pricing_options=_cpm_pricing_option(1.2),
        ),
    ]

    # Merge operator-declared signals from tenant_signals. This is the
    # publisher's first-party adapter capability map (custom KVs, audience
    # segments, weather signals, …) projected onto AdCP Signal shape so the
    # storefront sees the same vocabulary it would from any signals agent.
    # adapter_config stays operator-side; the wire response carries
    # value_type / categories / range only.
    tenant_ad_server = tenant.get("ad_server") if isinstance(tenant, dict) else getattr(tenant, "ad_server", None)
    tenant_agent_url = (
        tenant.get("public_agent_url") if isinstance(tenant, dict) else getattr(tenant, "public_agent_url", None)
    )
    assert identity.tenant_id is not None  # resolved by transport wrapper
    sample_signals.extend(
        _load_tenant_signals(
            identity.tenant_id,
            ad_server=tenant_ad_server,
            agent_url=tenant_agent_url,
        )
    )

    # Filter based on request parameters using AdCP-compliant fields
    for signal in sample_signals:
        # Apply signal_spec filter (natural language description matching)
        if req.signal_spec:
            spec_lower = req.signal_spec.lower()
            if (
                spec_lower not in signal.name.lower()
                and spec_lower not in signal.description.lower()
                and spec_lower not in signal.signal_type.value.lower()
            ):
                continue

        # Apply filters if provided
        if req.filters:
            # Filter by catalog_types (equivalent to old 'type' field)
            # signal.signal_type is SignalCatalogType enum; req.filters.catalog_types
            # is also list[SignalCatalogType] — compare enum-to-enum directly.
            if req.filters.catalog_types and signal.signal_type not in req.filters.catalog_types:
                continue

            # Filter by data_providers
            if req.filters.data_providers and signal.data_provider not in req.filters.data_providers:
                continue

            # Filter by max_cpm against the first pricing option (adcp 4.4
            # replaced the singleton ``pricing`` field with ``pricing_options``).
            if req.filters.max_cpm is not None and signal.pricing_options:
                first_cpm = signal.pricing_options[0].cpm
                if first_cpm is not None and first_cpm > req.filters.max_cpm:
                    continue

            # Filter by min_coverage_percentage
            if (
                req.filters.min_coverage_percentage is not None
                and signal.coverage_percentage < req.filters.min_coverage_percentage
            ):
                continue

        signals.append(signal)

    # Apply max_results limit (AdCP-compliant field name)
    if req.max_results:
        signals = signals[: req.max_results]

    # Signals are already constructed as local types (extending library types),
    # so no conversion needed — pass directly to response.
    return GetSignalsResponse(signals=signals, errors=None, context=req.context)


async def _activate_signal_impl(
    signal_agent_segment_id: str,
    campaign_id: str = None,
    media_buy_id: str = None,
    context: dict | None = None,  # payload-level context
    identity: ResolvedIdentity | None = None,
) -> ActivateSignalResponse:
    """Shared implementation for activate_signal (used by both MCP and A2A).

    Args:
        signal_agent_segment_id: Universal signal identifier to activate
        campaign_id: Optional campaign ID to activate signal for
        media_buy_id: Optional media buy ID to activate signal for
        context: Application level context per adcp spec
        identity: Resolved identity from transport boundary

    Returns:
        ActivateSignalResponse with activation status
    """
    start_time = time.time()

    # Authentication required for signal activation
    principal_id = identity.principal_id if identity else None

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    if not identity or not identity.tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Get the Principal object with ad server mappings
    if not principal_id:
        raise AdCPAuthenticationError("Authentication required for signal activation")
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)

    # Apply testing hooks
    if not identity:
        raise AdCPValidationError("Context required for signal activation", recovery="terminal")
    testing_ctx = identity.testing_context if identity else AdCPTestContext()
    campaign_info = {"endpoint": "activate_signal", "signal_id": signal_agent_segment_id}
    # Note: apply_testing_hooks modifies response data dict, not called here as no response yet

    try:
        from src.core.database.repositories.uow import TenantSignalUoW
        from src.core.schemas import Error

        # Operator-declared signals (the publisher's first-party adapter
        # capability map) are immediately usable on the publisher's own
        # inventory — no external provisioning. ``activate_signal`` validates
        # the signal exists and returns a stable handle the buyer can pass
        # in ``audience_include`` / ``audience_exclude`` on
        # ``create_media_buy``. For these signals, the
        # decisioning_platform_segment_id is the signal_id itself: stable
        # across calls, no synthetic UUID drift.
        assert identity.tenant_id is not None  # resolved by transport wrapper
        with TenantSignalUoW(identity.tenant_id) as uow:
            assert uow.tenant_signals is not None
            tenant_signal = uow.tenant_signals.get_by_id(signal_agent_segment_id)
            # Snapshot the stable signal_id while the row is still
            # session-bound — accessing it after the UoW exits would trip
            # DetachedInstanceError on attribute refresh.
            resolved_signal_id = tenant_signal.signal_id if tenant_signal is not None else None

        if resolved_signal_id is not None:
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
                activation_details={
                    "decisioning_platform_segment_id": resolved_signal_id,
                    "estimated_activation_duration_minutes": 0.0,
                    "status": "deployed",
                },
                errors=None,
                context=context,
            )

        # Fall-through: signal not declared on tenant_signals. Today this
        # covers the hardcoded sample signals in get_signals (legacy demo
        # data) — they get the mock activation flow. A future signals-agent
        # path would call out to an external agent here; for now we preserve
        # the demo behavior so existing buyers don't break.
        if signal_agent_segment_id.startswith("premium_"):
            return ActivateSignalResponse(
                signal_id=signal_agent_segment_id,
                activation_details=None,
                errors=[
                    Error(
                        code="APPROVAL_REQUIRED",
                        message=f"Signal {signal_agent_segment_id} requires manual approval before activation",
                    )
                ],
                context=context,
            )

        decisioning_platform_segment_id = f"seg_{signal_agent_segment_id}_{uuid.uuid4().hex[:8]}"
        return ActivateSignalResponse(
            signal_id=signal_agent_segment_id,
            activation_details={
                "decisioning_platform_segment_id": decisioning_platform_segment_id,
                "estimated_activation_duration_minutes": 15.0,
                "status": "processing",
            },
            errors=None,
            context=context,
        )

    except Exception as e:
        logger.error(f"Error activating signal {signal_agent_segment_id}: {e}")
        from src.core.schemas import Error

        return ActivateSignalResponse(
            signal_id=signal_agent_segment_id,
            activation_details=None,
            errors=[Error(code="ACTIVATION_ERROR", message=str(e))],
            context=context,
        )
