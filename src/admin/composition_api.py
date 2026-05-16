"""Embedded Composition API — REST blueprint at ``/api/v1/tenants/<tenant_id>/...``.

Server-to-server surface for an embedding storefront (e.g. Scope3) to manage
the primitives it composes products from: inventory profiles, tenant
signals (operator's map of adapter targeting capabilities), advertiser
mappings, and dynamic product creation.

Auth: same operator/wrapper API key as the Tenant Management API
(``X-Tenant-Management-API-Key``). No new MCP tools — REST only.

Adapter-agnostic at the storefront boundary:
- ``GET /inventory-profiles`` returns AdCP-vocab metadata only (no
  adapter-shaped fields). Operators author the adapter-specific
  ``inventory_config`` on Create/Update; storefront never sees it.
- Targeting is expressed as ``SignalSelection`` over the operator's
  declared ``TenantSignal`` catalog. Each signal carries AdCP ``Signal``
  shape (``value_type``, ``categories``, ``range``); the per-adapter
  materializer resolves selections into adapter ``implementation_config``
  at compose time.

In embedded mode the host (storefront) is the only agent. The sales
agent never receives requests directly from a buyer, so there is no
per-buyer principal API on this surface — the tenant's embedded principal
is auto-resolved (lazy-created on first compose).

See ``.context/embedded-composition-design.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from src.admin.api_schemas.composition import (
    AdvertiserListResponse,
    AdvertiserMappingCreate,
    AdvertiserMappingListResponse,
    AdvertiserMappingRead,
    AdvertiserMappingUpdate,
    AdvertiserSummary,
    ApiError,
    CompositionError,
    CompositionErrorDetail,
    DynamicProductCreate,
    DynamicProductRead,
    InventoryProfileCreate,
    InventoryProfileListResponse,
    InventoryProfileRead,
    InventoryProfileUpdate,
    SignalRange,
    SignalSelection,
    TenantSignalCreate,
    TenantSignalListResponse,
    TenantSignalRead,
    TenantSignalUpdate,
)
from src.admin.auth_helpers import require_api_key_auth
from src.admin.composition_materializers import (
    MaterializationError,
    MaterializerContext,
    supported_adapters,
)
from src.admin.composition_materializers import (
    get as get_materializer,
)
from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdvertiserRoutingRule,
    InventoryProfile,
    PricingOption,
    Principal,
    Product,
    Tenant,
    TenantSignal,
)
from src.core.database.repositories.advertiser_mapping import (
    AdvertiserMappingRepository,
    GamAdvertiserRepository,
)
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository

logger = logging.getLogger(__name__)

composition_api = Blueprint(
    "composition_api",
    __name__,
    url_prefix="/api/v1",
)

require_composition_api_key = require_api_key_auth(
    env_var="TENANT_MANAGEMENT_API_KEY",
    config_key="tenant_management_api_key",
    header="X-Tenant-Management-API-Key",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _api_error(code: str, message: str, status: int, details: dict | None = None):
    body = ApiError(error=code, message=message, details=details).model_dump(exclude_none=True)
    return jsonify(body), status


def _parse_updated_since() -> datetime | None:
    raw = request.args.get("updated_since")
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _compute_etag(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _maybe_304(etag: str):
    inm = request.headers.get("If-None-Match")
    if inm and inm.strip('"') == etag:
        return ("", 304, {"ETag": f'"{etag}"'})
    return None


# ---------------------------------------------------------------------------
# Inventory profiles
# ---------------------------------------------------------------------------


def _inventory_profile_to_read(profile: InventoryProfile) -> dict:
    """Storefront-facing read. Adapter-shaped fields (``inventory_config``,
    ``format_ids``, ``publisher_properties``, ``targeting_template``) are
    intentionally omitted — operators manage them, storefront composes
    against the AdCP-vocab metadata only."""
    return InventoryProfileRead(
        profile_id=profile.profile_id,
        name=profile.name,
        description=profile.description,
        constraints=profile.constraints,
        etag=profile.etag,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    ).model_dump(mode="json")


def _refresh_inventory_profile_etag(profile: InventoryProfile) -> None:
    profile.etag = _compute_etag(_inventory_profile_to_read(profile))


@composition_api.route("/tenants/<tenant_id>/inventory-profiles", methods=["GET"])
@require_composition_api_key
def list_inventory_profiles(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = InventoryProfileRepository(session, tenant_id)
        profiles = repo.list_all(updated_since=_parse_updated_since())
        items = [_inventory_profile_to_read(p) for p in profiles]
        body = InventoryProfileListResponse(inventory_profiles=items).model_dump(mode="json")
        etag = _compute_etag(body)
        not_modified = _maybe_304(etag)
        if not_modified:
            return not_modified
        return jsonify(body), 200, {"ETag": f'"{etag}"'}


@composition_api.route("/tenants/<tenant_id>/inventory-profiles/<profile_id>", methods=["GET"])
@require_composition_api_key
def get_inventory_profile(tenant_id: str, profile_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = InventoryProfileRepository(session, tenant_id)
        profile = repo.get_by_id(profile_id)
        if profile is None:
            return _api_error(
                "inventory_profile_not_found",
                f"Inventory profile {profile_id!r} not found.",
                404,
            )
        body = _inventory_profile_to_read(profile)
        etag = profile.etag or _compute_etag(body)
        not_modified = _maybe_304(etag)
        if not_modified:
            return not_modified
        return jsonify(body), 200, {"ETag": f'"{etag}"'}


@composition_api.route("/tenants/<tenant_id>/inventory-profiles", methods=["POST"])
@require_composition_api_key
def create_inventory_profile(tenant_id: str):
    try:
        payload = InventoryProfileCreate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = InventoryProfileRepository(session, tenant_id)
        if repo.get_by_id(payload.profile_id) is not None:
            return _api_error(
                "conflict",
                f"Inventory profile {payload.profile_id!r} already exists.",
                409,
            )
        profile = InventoryProfile(
            tenant_id=tenant_id,
            profile_id=payload.profile_id,
            name=payload.name,
            description=payload.description,
            inventory_config=payload.inventory_config,
            format_ids=payload.format_ids,
            publisher_properties=payload.publisher_properties,
            targeting_template=payload.targeting_template,
            constraints=payload.constraints.model_dump() if payload.constraints else None,
        )
        repo.add(profile)
        session.flush()
        _refresh_inventory_profile_etag(profile)
        session.commit()
        body = _inventory_profile_to_read(profile)
        return jsonify(body), 201, {"ETag": f'"{profile.etag}"'}


@composition_api.route("/tenants/<tenant_id>/inventory-profiles/<profile_id>", methods=["PUT"])
@require_composition_api_key
def update_inventory_profile(tenant_id: str, profile_id: str):
    try:
        payload = InventoryProfileUpdate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = InventoryProfileRepository(session, tenant_id)
        profile = repo.get_by_id(profile_id)
        if profile is None:
            return _api_error(
                "inventory_profile_not_found",
                f"Inventory profile {profile_id!r} not found.",
                404,
            )
        for field in (
            "name",
            "description",
            "inventory_config",
            "format_ids",
            "publisher_properties",
            "targeting_template",
        ):
            value = getattr(payload, field)
            if value is not None:
                setattr(profile, field, value)
        if payload.constraints is not None:
            profile.constraints = payload.constraints.model_dump()
        _refresh_inventory_profile_etag(profile)
        session.commit()
        return jsonify(_inventory_profile_to_read(profile)), 200, {"ETag": f'"{profile.etag}"'}


@composition_api.route("/tenants/<tenant_id>/inventory-profiles/<profile_id>", methods=["DELETE"])
@require_composition_api_key
def delete_inventory_profile(tenant_id: str, profile_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = InventoryProfileRepository(session, tenant_id)
        profile = repo.get_by_id(profile_id)
        if profile is None:
            return _api_error(
                "inventory_profile_not_found",
                f"Inventory profile {profile_id!r} not found.",
                404,
            )
        repo.delete(profile)
        session.commit()
        return "", 204


# ---------------------------------------------------------------------------
# Tenant signals (operator-authored capability map; storefront-visible)
# ---------------------------------------------------------------------------


def _signal_range(signal: TenantSignal) -> SignalRange | None:
    if signal.range_min is None and signal.range_max is None:
        return None
    return SignalRange(min=signal.range_min, max=signal.range_max)


def _signal_to_read(signal: TenantSignal) -> dict:
    """Storefront-facing read. AdCP-vocab fields only — ``adapter_config`` is
    operator-authored and never echoed."""
    return TenantSignalRead(
        signal_id=signal.signal_id,
        name=signal.name,
        description=signal.description,
        value_type=signal.value_type,
        categories=list(signal.categories or []),
        range=_signal_range(signal),
        data_provider=signal.data_provider,
        targeting_dimension=signal.targeting_dimension,
        etag=signal.etag,
        created_at=signal.created_at,
        updated_at=signal.updated_at,
    ).model_dump(mode="json")


def _refresh_signal_etag(signal: TenantSignal) -> None:
    signal.etag = _compute_etag(_signal_to_read(signal))


@composition_api.route("/tenants/<tenant_id>/signals", methods=["GET"])
@require_composition_api_key
def list_signals(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = TenantSignalRepository(session, tenant_id)
        rows = repo.list_all(updated_since=_parse_updated_since())
        items = [_signal_to_read(s) for s in rows]
        body = TenantSignalListResponse(signals=items).model_dump(mode="json")
        etag = _compute_etag(body)
        not_modified = _maybe_304(etag)
        if not_modified:
            return not_modified
        return jsonify(body), 200, {"ETag": f'"{etag}"'}


@composition_api.route("/tenants/<tenant_id>/signals/<signal_id>", methods=["GET"])
@require_composition_api_key
def get_signal(tenant_id: str, signal_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            return _api_error("signal_not_found", f"Signal {signal_id!r} not found.", 404)
        body = _signal_to_read(signal)
        etag = signal.etag or _compute_etag(body)
        not_modified = _maybe_304(etag)
        if not_modified:
            return not_modified
        return jsonify(body), 200, {"ETag": f'"{etag}"'}


@composition_api.route("/tenants/<tenant_id>/signals", methods=["POST"])
@require_composition_api_key
def create_signal(tenant_id: str):
    try:
        payload = TenantSignalCreate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = TenantSignalRepository(session, tenant_id)
        if repo.get_by_id(payload.signal_id) is not None:
            return _api_error("conflict", f"Signal {payload.signal_id!r} already exists.", 409)
        signal = TenantSignal(
            tenant_id=tenant_id,
            signal_id=payload.signal_id,
            name=payload.name,
            description=payload.description,
            value_type=payload.value_type,
            categories=list(payload.categories),
            range_min=payload.range.min if payload.range else None,
            range_max=payload.range.max if payload.range else None,
            adapter_config=payload.adapter_config,
            data_provider=payload.data_provider,
            targeting_dimension=payload.targeting_dimension,
        )
        repo.add(signal)
        session.flush()
        _refresh_signal_etag(signal)
        session.commit()
        return jsonify(_signal_to_read(signal)), 201, {"ETag": f'"{signal.etag}"'}


@composition_api.route("/tenants/<tenant_id>/signals/<signal_id>", methods=["PUT"])
@require_composition_api_key
def update_signal(tenant_id: str, signal_id: str):
    try:
        payload = TenantSignalUpdate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            return _api_error("signal_not_found", f"Signal {signal_id!r} not found.", 404)
        if payload.name is not None:
            signal.name = payload.name
        if payload.description is not None:
            signal.description = payload.description
        if payload.value_type is not None:
            signal.value_type = payload.value_type
        if payload.categories is not None:
            signal.categories = list(payload.categories)
        if payload.range is not None:
            signal.range_min = payload.range.min
            signal.range_max = payload.range.max
        if payload.adapter_config is not None:
            signal.adapter_config = payload.adapter_config
        if payload.data_provider is not None:
            signal.data_provider = payload.data_provider
        if payload.targeting_dimension is not None:
            signal.targeting_dimension = payload.targeting_dimension
        _refresh_signal_etag(signal)
        session.commit()
        return jsonify(_signal_to_read(signal)), 200, {"ETag": f'"{signal.etag}"'}


@composition_api.route("/tenants/<tenant_id>/signals/<signal_id>", methods=["DELETE"])
@require_composition_api_key
def delete_signal(tenant_id: str, signal_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            return _api_error("signal_not_found", f"Signal {signal_id!r} not found.", 404)
        repo.delete(signal)
        session.commit()
        return "", 204


# ---------------------------------------------------------------------------
# Embedded principal — lazy auto-resolution
# ---------------------------------------------------------------------------


_EMBEDDED_PRINCIPAL_EXTERNAL_ID = "__embedded_host__"


def _resolve_or_create_embedded_principal(session, tenant: Tenant) -> Principal:
    repo = PrincipalRepository(session, tenant.tenant_id)
    existing = repo.get_by_external_id(_EMBEDDED_PRINCIPAL_EXTERNAL_ID)
    if existing is not None:
        return existing

    adapter_key = tenant.ad_server if tenant.ad_server in {"google_ad_manager", "mock"} else "mock"
    sentinel_mapping = {adapter_key: {"resolved_via": "advertiser_routing_rules"}}

    principal = Principal(
        tenant_id=tenant.tenant_id,
        principal_id=f"embedded_{secrets.token_hex(6)}",
        external_id=_EMBEDDED_PRINCIPAL_EXTERNAL_ID,
        name=f"{tenant.name} embedded host",
        platform_mappings=sentinel_mapping,
        access_token=None,
    )
    repo.add(principal)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        replay = repo.get_by_external_id(_EMBEDDED_PRINCIPAL_EXTERNAL_ID)
        if replay is None:
            raise
        return replay
    return principal


# ---------------------------------------------------------------------------
# Compose helpers
# ---------------------------------------------------------------------------


DEFAULT_TTL_SECONDS = 7 * 24 * 3600
SUPPORTED_PRICING_MODELS: frozenset[str] = frozenset({"cpm"})


def _compute_expires_at(
    *,
    flight_start: date,
    requested_ttl_seconds: int | None,
    tenant_max_ttl_seconds: int | None,
) -> datetime:
    now = datetime.now(UTC)
    candidates: list[datetime] = [datetime.combine(flight_start, datetime.min.time(), tzinfo=UTC)]
    if tenant_max_ttl_seconds and tenant_max_ttl_seconds > 0:
        candidates.append(now + timedelta(seconds=tenant_max_ttl_seconds))
    if requested_ttl_seconds and requested_ttl_seconds > 0:
        candidates.append(now + timedelta(seconds=requested_ttl_seconds))
    if len(candidates) == 1:
        candidates.append(now + timedelta(seconds=DEFAULT_TTL_SECONDS))
    return min(candidates)


def _validate_signal_narrowing(
    *,
    inventory_profile: InventoryProfile,
    signals: list[TenantSignal],
) -> list[CompositionErrorDetail]:
    """Each signal's ``targeting_dimension`` must be in the inventory profile's
    allowed ``constraints.targeting_dimensions`` (when both are declared)."""
    constraints = inventory_profile.constraints or {}
    allowed = constraints.get("targeting_dimensions")
    if not allowed:
        return []
    allowed_set = set(allowed)
    errors: list[CompositionErrorDetail] = []
    for signal in signals:
        dim = signal.targeting_dimension
        if dim and dim not in allowed_set:
            errors.append(
                CompositionErrorDetail(
                    code="dimension_unsupported",
                    inventory_profile_id=inventory_profile.profile_id,
                    signal_id=signal.signal_id,
                    dimension=dim,
                    message=(
                        f"Inventory profile does not support targeting dimension {dim!r} (signal {signal.signal_id!r})."
                    ),
                )
            )
    return errors


def _persist_pricing_option(
    *,
    session,
    tenant_id: str,
    product_id: str,
    pricing_option,
) -> PricingOption:
    po_dict = pricing_option.model_dump(mode="json")
    rate: Decimal | None
    if pricing_option.fixed_price is not None:
        rate = Decimal(str(pricing_option.fixed_price))
        is_fixed = True
    elif pricing_option.floor_price is not None:
        rate = Decimal(str(pricing_option.floor_price))
        is_fixed = False
    else:
        rate = None
        is_fixed = False
    parameters: dict[str, Any] = {"_pricing_option": po_dict}
    min_spend = (
        Decimal(str(pricing_option.min_spend_per_package)) if pricing_option.min_spend_per_package is not None else None
    )
    price_guidance = pricing_option.price_guidance.model_dump() if pricing_option.price_guidance else None
    po_row = PricingOption(
        tenant_id=tenant_id,
        product_id=product_id,
        pricing_model=pricing_option.pricing_model,
        rate=rate,
        currency=pricing_option.currency,
        is_fixed=is_fixed,
        price_guidance=price_guidance,
        parameters=parameters,
        min_spend_per_package=min_spend,
    )
    session.add(po_row)
    return po_row


def _read_pricing_option(po_row: PricingOption) -> dict:
    params = po_row.parameters or {}
    cached = params.get("_pricing_option")
    if cached:
        return cached
    return {
        "pricing_option_id": f"po_{po_row.id}",
        "pricing_model": po_row.pricing_model,
        "currency": po_row.currency,
        "fixed_price": float(po_row.rate) if po_row.is_fixed and po_row.rate else None,
        "floor_price": float(po_row.rate) if not po_row.is_fixed and po_row.rate else None,
    }


def _read_signal_selections(product: Product) -> list[dict]:
    """Snapshot of the storefront's signal selections, stored on
    ``Product.implementation_config['_signal_selections']`` at compose time."""
    config = product.implementation_config or {}
    return list(config.get("_signal_selections") or [])


def _dynamic_product_to_read(
    product: Product,
    pricing_option_dict: dict,
    signal_selections: list[dict],
) -> dict:
    flight_start = (product.expires_at and product.expires_at.date()) or date.today()
    return DynamicProductRead(
        product_id=product.product_id,
        composition_source="storefront_composed",
        inventory_profile_id=(product.inventory_profile.profile_id if product.inventory_profile else ""),
        signal_selections=[SignalSelection.model_validate(s) for s in signal_selections],
        pricing_option=pricing_option_dict,
        flight_start=flight_start,
        flight_end=flight_start,
        expires_at=product.expires_at or datetime.now(UTC),
        implementation_config_summary={
            k: v for k, v in (product.implementation_config or {}).items() if not k.startswith("_")
        },
        created_at=getattr(product, "created_at", datetime.now(UTC)),
    ).model_dump(mode="json")


@composition_api.route("/tenants/<tenant_id>/products", methods=["POST"])
@require_composition_api_key
def compose_product(tenant_id: str):
    """Materialize a dynamic product from primitives. Idempotency-Key required.

    Inputs: ``inventory_profile_id`` + ``signal_selections`` (over
    operator-declared signals) + ``pricing_option`` + dates. Adapter-agnostic;
    materialization dispatches on ``tenant.ad_server``.
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return _api_error(
            "missing_idempotency_key",
            "Idempotency-Key header is required on POST /api/v1/products.",
            400,
        )

    try:
        payload = DynamicProductCreate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    if payload.flight_end < payload.flight_start:
        return (
            jsonify(
                CompositionError(
                    details=[
                        CompositionErrorDetail(
                            code="invalid_flight_dates",
                            message="flight_end must be on or after flight_start.",
                        )
                    ]
                ).model_dump(mode="json")
            ),
            422,
        )

    if payload.pricing_option.pricing_model not in SUPPORTED_PRICING_MODELS:
        return (
            jsonify(
                CompositionError(
                    details=[
                        CompositionErrorDetail(
                            code="unsupported_pricing_model",
                            pricing_model=payload.pricing_option.pricing_model,
                            expected=",".join(sorted(SUPPORTED_PRICING_MODELS)),
                            got=payload.pricing_option.pricing_model,
                            message=(
                                f"pricing_model={payload.pricing_option.pricing_model!r} is not in "
                                "the agent's declared supported_pricing_models."
                            ),
                        )
                    ]
                ).model_dump(mode="json")
            ),
            422,
        )

    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)

        materializer = get_materializer(tenant.ad_server or "")
        if materializer is None:
            return (
                jsonify(
                    CompositionError(
                        details=[
                            CompositionErrorDetail(
                                code="unsupported_adapter",
                                adapter=tenant.ad_server,
                                expected=",".join(supported_adapters()),
                                got=tenant.ad_server or "",
                                message=(
                                    f"Tenant ad_server={tenant.ad_server!r} has no composition materializer registered."
                                ),
                            )
                        ]
                    ).model_dump(mode="json")
                ),
                422,
            )

        embedded_principal = _resolve_or_create_embedded_principal(session, tenant)
        products = ProductRepository(session, tenant_id)

        existing = products.find_composed_by_idempotency_key(
            principal_id=embedded_principal.principal_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            existing_pricing = _read_pricing_option(existing.pricing_options[0]) if existing.pricing_options else {}
            return (
                jsonify(_dynamic_product_to_read(existing, existing_pricing, _read_signal_selections(existing))),
                200,
            )

        inv_repo = InventoryProfileRepository(session, tenant_id)
        inventory_profile = inv_repo.get_by_id(payload.inventory_profile_id)
        if inventory_profile is None:
            return (
                jsonify(
                    CompositionError(
                        details=[
                            CompositionErrorDetail(
                                code="unknown_inventory_profile",
                                inventory_profile_id=payload.inventory_profile_id,
                                message=f"Inventory profile {payload.inventory_profile_id!r} not found.",
                            )
                        ]
                    ).model_dump(mode="json")
                ),
                422,
            )

        signal_repo = TenantSignalRepository(session, tenant_id)
        selected_ids = [s.signal_id for s in payload.signal_selections]
        signals = signal_repo.list_by_ids(selected_ids) if selected_ids else []
        if len(signals) != len(set(selected_ids)):
            found_ids = {s.signal_id for s in signals}
            missing = [sid for sid in selected_ids if sid not in found_ids]
            return (
                jsonify(
                    CompositionError(
                        details=[
                            CompositionErrorDetail(
                                code="unknown_signal",
                                signal_id=sid,
                                message=f"Signal {sid!r} not declared on tenant.",
                            )
                            for sid in missing
                        ]
                    ).model_dump(mode="json")
                ),
                422,
            )

        narrowing_errors = _validate_signal_narrowing(inventory_profile=inventory_profile, signals=signals)
        if narrowing_errors:
            return (
                jsonify(CompositionError(details=narrowing_errors).model_dump(mode="json")),
                422,
            )

        signals_by_id = {s.signal_id: s for s in signals}
        ctx = MaterializerContext(
            inventory_profile=inventory_profile,
            signal_selections=list(payload.signal_selections),
            signals_by_id=signals_by_id,
            adcp_targeting=payload.adcp_targeting,
        )
        try:
            implementation_config = materializer.materialize(ctx)
        except MaterializationError as exc:
            return (
                jsonify(CompositionError(details=exc.details).model_dump(mode="json")),
                422,
            )

        # Snapshot the storefront's signal_selections trail onto the
        # materialized config so GET round-trips return what was composed
        # (the adapter ignores keys it doesn't recognize).
        implementation_config["_signal_selections"] = [s.model_dump(mode="json") for s in payload.signal_selections]

        expires_at = _compute_expires_at(
            flight_start=payload.flight_start,
            requested_ttl_seconds=payload.ttl_seconds,
            tenant_max_ttl_seconds=tenant.max_composition_ttl_seconds,
        )

        product_id = f"dyn_{secrets.token_hex(10)}"
        product = Product(
            tenant_id=tenant_id,
            product_id=product_id,
            name=f"Composed {product_id}",
            description=None,
            format_ids=inventory_profile.format_ids or [],
            targeting_template=payload.adcp_targeting or inventory_profile.targeting_template or {},
            delivery_type="non_guaranteed",
            implementation_config=implementation_config,
            inventory_profile_id=inventory_profile.id,
            property_tags=["all_inventory"],
            expires_at=expires_at,
            composition_source="storefront_composed",
            composed_by_principal_id=embedded_principal.principal_id,
            idempotency_key=idempotency_key,
            allowed_principal_ids=[embedded_principal.principal_id],
        )
        products.create(product)
        _persist_pricing_option(
            session=session,
            tenant_id=tenant_id,
            product_id=product_id,
            pricing_option=payload.pricing_option,
        )

        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            replay = products.find_composed_by_idempotency_key(
                principal_id=embedded_principal.principal_id,
                idempotency_key=idempotency_key,
            )
            if replay is not None:
                replay_pricing = _read_pricing_option(replay.pricing_options[0]) if replay.pricing_options else {}
                return (
                    jsonify(_dynamic_product_to_read(replay, replay_pricing, _read_signal_selections(replay))),
                    200,
                )
            return _api_error("conflict", str(exc), 409)

        pricing_option_dict = payload.pricing_option.model_dump(mode="json")
        signal_selections_dump = [s.model_dump(mode="json") for s in payload.signal_selections]
        response_body = _dynamic_product_to_read(product, pricing_option_dict, signal_selections_dump)
        audit_details = {
            "product_id": product.product_id,
            "inventory_profile_id": inventory_profile.profile_id,
            "signal_selections": signal_selections_dump,
            "pricing_option": pricing_option_dict,
            "idempotency_key": idempotency_key,
            "expires_at": expires_at.isoformat(),
            "adapter": tenant.ad_server,
        }
        principal_name = embedded_principal.name
        principal_id_value = embedded_principal.principal_id

    try:
        get_audit_logger("composition_api", tenant_id=tenant_id).log_operation(
            operation="compose_product",
            principal_name=principal_name,
            principal_id=principal_id_value,
            adapter_id=None,
            success=True,
            details=audit_details,
        )
    except Exception:
        logger.exception("Audit log emission failed for composed product %s", audit_details["product_id"])

    return jsonify(response_body), 201


@composition_api.route("/tenants/<tenant_id>/products/<product_id>", methods=["GET"])
@require_composition_api_key
def get_composed_product(tenant_id: str, product_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        products = ProductRepository(session, tenant_id)
        product = products.get_by_id_with_pricing(product_id)
        if product is None or product.composition_source != "storefront_composed":
            return _api_error(
                "product_not_found",
                f"Composed product {product_id!r} not found.",
                404,
            )
        pricing_option_dict = _read_pricing_option(product.pricing_options[0]) if product.pricing_options else {}
        return (
            jsonify(_dynamic_product_to_read(product, pricing_option_dict, _read_signal_selections(product))),
            200,
        )


# ---------------------------------------------------------------------------
# Advertiser mappings (AccountReference → adapter advertiser)
# ---------------------------------------------------------------------------


def _account_from_rule(rule: AdvertiserRoutingRule) -> dict:
    account: dict[str, Any] = {"operator": rule.operator_domain, "sandbox": False}
    if rule.brand_house is not None or rule.brand_id is not None:
        brand: dict[str, Any] = {}
        if rule.brand_house is not None:
            brand["domain"] = rule.brand_house
        if rule.brand_id is not None:
            brand["brand_id"] = rule.brand_id
        account["brand"] = brand
    return account


def _advertiser_mapping_to_read(rule: AdvertiserRoutingRule) -> dict:
    return AdvertiserMappingRead(
        mapping_id=rule.id,
        account=_account_from_rule(rule),
        adapter_advertiser_id=rule.gam_advertiser_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    ).model_dump(mode="json")


def _account_to_columns(account) -> tuple[str, str | None, str | None]:
    operator_domain = account.operator
    brand = account.brand
    brand_house = brand.domain if brand else None
    brand_id = brand.brand_id if brand else None
    return operator_domain, brand_house, brand_id


@composition_api.route("/tenants/<tenant_id>/advertiser-mappings", methods=["GET"])
@require_composition_api_key
def list_advertiser_mappings(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = AdvertiserMappingRepository(session, tenant_id)
        items = [_advertiser_mapping_to_read(rule) for rule in repo.list_all()]
        body = AdvertiserMappingListResponse(advertiser_mappings=items).model_dump(mode="json")
        return jsonify(body), 200


@composition_api.route(
    "/tenants/<tenant_id>/advertiser-mappings/<mapping_id>",
    methods=["GET"],
)
@require_composition_api_key
def get_advertiser_mapping(tenant_id: str, mapping_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        rule = AdvertiserMappingRepository(session, tenant_id).get_by_id(mapping_id)
        if rule is None:
            return _api_error(
                "advertiser_mapping_not_found",
                f"Advertiser mapping {mapping_id!r} not found.",
                404,
            )
        return jsonify(_advertiser_mapping_to_read(rule)), 200


@composition_api.route("/tenants/<tenant_id>/advertiser-mappings", methods=["POST"])
@require_composition_api_key
def create_advertiser_mapping(tenant_id: str):
    try:
        payload = AdvertiserMappingCreate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)
    operator_domain, brand_house, brand_id = _account_to_columns(payload.account)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = AdvertiserMappingRepository(session, tenant_id)
        existing = repo.find_by_natural_key(
            principal_id=None,
            operator_domain=operator_domain,
            brand_house=brand_house,
            brand_id=brand_id,
        )
        if existing is not None:
            return _api_error(
                "conflict",
                "An advertiser mapping already exists for this account natural key.",
                409,
                details={"mapping_id": existing.id},
            )

        rule = AdvertiserRoutingRule(
            id=f"rule_{secrets.token_hex(10)}",
            tenant_id=tenant_id,
            principal_id=None,
            operator_domain=operator_domain,
            brand_house=brand_house,
            brand_id=brand_id,
            gam_advertiser_id=payload.adapter_advertiser_id,
        )
        repo.add(rule)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            return _api_error("conflict", str(exc), 409)
        return jsonify(_advertiser_mapping_to_read(rule)), 201


@composition_api.route(
    "/tenants/<tenant_id>/advertiser-mappings/<mapping_id>",
    methods=["PUT"],
)
@require_composition_api_key
def update_advertiser_mapping(tenant_id: str, mapping_id: str):
    try:
        payload = AdvertiserMappingUpdate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        rule = AdvertiserMappingRepository(session, tenant_id).get_by_id(mapping_id)
        if rule is None:
            return _api_error(
                "advertiser_mapping_not_found",
                f"Advertiser mapping {mapping_id!r} not found.",
                404,
            )
        if payload.adapter_advertiser_id is not None:
            rule.gam_advertiser_id = payload.adapter_advertiser_id
        session.commit()
        return jsonify(_advertiser_mapping_to_read(rule)), 200


@composition_api.route(
    "/tenants/<tenant_id>/advertiser-mappings/<mapping_id>",
    methods=["DELETE"],
)
@require_composition_api_key
def delete_advertiser_mapping(tenant_id: str, mapping_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = AdvertiserMappingRepository(session, tenant_id)
        rule = repo.get_by_id(mapping_id)
        if rule is None:
            return _api_error(
                "advertiser_mapping_not_found",
                f"Advertiser mapping {mapping_id!r} not found.",
                404,
            )
        repo.delete(rule)
        session.commit()
        return "", 204


# ---------------------------------------------------------------------------
# Advertisers (synced cache, read-only)
# ---------------------------------------------------------------------------


@composition_api.route("/tenants/<tenant_id>/advertisers", methods=["GET"])
@require_composition_api_key
def list_advertisers(tenant_id: str):
    include_inactive = request.args.get("include_inactive", "").lower() in ("true", "1", "yes")
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = GamAdvertiserRepository(session, tenant_id)
        rows = repo.list_all(include_inactive=include_inactive)
        items = [
            AdvertiserSummary(
                adapter_advertiser_id=row.advertiser_id,
                name=row.name,
                status=row.status,
                currency_code=row.currency_code,
                synced_at=row.synced_at,
            ).model_dump(mode="json")
            for row in rows
        ]
        body = AdvertiserListResponse(advertisers=items).model_dump(mode="json")
        return jsonify(body), 200
