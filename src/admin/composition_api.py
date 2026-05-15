"""Embedded Composition API — REST blueprint at ``/api/v1/tenants/<tenant_id>/...``.

Server-to-server surface for an embedding storefront (e.g. Scope3) to manage
the primitives it composes products from: inventory profiles, custom targeting
profiles, advertiser mappings, and dynamic product creation.

Auth: same operator/wrapper API key as the Tenant Management API
(``X-Tenant-Management-API-Key``). No new MCP tools — this is REST only.

Vocabulary mirrors the AdCP spec (``PricingOption``, ``AccountReference``) so
storefronts can paste the same shapes they already construct for the buyer
protocol. Pricing flows through ``PricingOption`` — the storefront sets the
price end-to-end; the sales agent records it (no floor enforcement).

In embedded mode the host (storefront) is the only agent. The sales agent
never receives requests directly from a buyer, so there is no per-buyer
principal API on this surface — the tenant's embedded principal is
auto-resolved (lazy-created on first compose).

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
    AudienceSegmentsResponse,
    CompositionError,
    CompositionErrorDetail,
    CustomTargetingKeysResponse,
    CustomTargetingKeyValuesResponse,
    CustomTargetingProfileCreate,
    CustomTargetingProfileListResponse,
    CustomTargetingProfileRead,
    CustomTargetingProfileUpdate,
    DynamicProductCreate,
    DynamicProductRead,
    InventoryProfileCreate,
    InventoryProfileListResponse,
    InventoryProfileRead,
    InventoryProfileUpdate,
    TargetingComponents,
)
from src.admin.auth_helpers import require_api_key_auth
from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AdvertiserRoutingRule,
    CustomTargetingProfile,
    InventoryProfile,
    PricingOption,
    Principal,
    Product,
    Tenant,
)
from src.core.database.repositories.advertiser_mapping import (
    AdvertiserMappingRepository,
    GamAdvertiserRepository,
)
from src.core.database.repositories.custom_targeting_profile import CustomTargetingProfileRepository
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.product import ProductRepository

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
    """Deterministic ETag over a JSON-serializable payload."""
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _maybe_304(etag: str):
    """Return a 304 if If-None-Match matches; otherwise None."""
    inm = request.headers.get("If-None-Match")
    if inm and inm.strip('"') == etag:
        return ("", 304, {"ETag": f'"{etag}"'})
    return None


def _ensure_tenant_or_404(tenant_id: str) -> Tenant | tuple[Any, int]:
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        return tenant


# ---------------------------------------------------------------------------
# Inventory profiles
# ---------------------------------------------------------------------------


def _inventory_profile_to_read(profile: InventoryProfile) -> dict:
    return InventoryProfileRead(
        profile_id=profile.profile_id,
        name=profile.name,
        description=profile.description,
        inventory_config=profile.inventory_config or {},
        format_ids=profile.format_ids or [],
        publisher_properties=profile.publisher_properties or [],
        targeting_template=profile.targeting_template,
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
# Custom targeting profiles
# ---------------------------------------------------------------------------


def _custom_targeting_profile_to_read(profile: CustomTargetingProfile) -> dict:
    components = TargetingComponents.model_validate(profile.components or {})
    return CustomTargetingProfileRead(
        custom_targeting_profile_id=profile.custom_targeting_profile_id,
        name=profile.name,
        description=profile.description,
        components=components,
        touches_dimensions=profile.touches_dimensions or [],
        etag=profile.etag,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    ).model_dump(mode="json")


def _refresh_custom_targeting_profile_etag(profile: CustomTargetingProfile) -> None:
    profile.etag = _compute_etag(_custom_targeting_profile_to_read(profile))


@composition_api.route("/tenants/<tenant_id>/custom-targeting-profiles", methods=["GET"])
@require_composition_api_key
def list_custom_targeting_profiles(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = CustomTargetingProfileRepository(session, tenant_id)
        profiles = repo.list_all(updated_since=_parse_updated_since())
        items = [_custom_targeting_profile_to_read(p) for p in profiles]
        body = CustomTargetingProfileListResponse(custom_targeting_profiles=items).model_dump(mode="json")
        etag = _compute_etag(body)
        not_modified = _maybe_304(etag)
        if not_modified:
            return not_modified
        return jsonify(body), 200, {"ETag": f'"{etag}"'}


@composition_api.route(
    "/tenants/<tenant_id>/custom-targeting-profiles/<profile_id>",
    methods=["GET"],
)
@require_composition_api_key
def get_custom_targeting_profile(tenant_id: str, profile_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = CustomTargetingProfileRepository(session, tenant_id)
        profile = repo.get_by_id(profile_id)
        if profile is None:
            return _api_error(
                "custom_targeting_profile_not_found",
                f"Custom targeting profile {profile_id!r} not found.",
                404,
            )
        body = _custom_targeting_profile_to_read(profile)
        etag = profile.etag or _compute_etag(body)
        not_modified = _maybe_304(etag)
        if not_modified:
            return not_modified
        return jsonify(body), 200, {"ETag": f'"{etag}"'}


@composition_api.route("/tenants/<tenant_id>/custom-targeting-profiles", methods=["POST"])
@require_composition_api_key
def create_custom_targeting_profile(tenant_id: str):
    try:
        payload = CustomTargetingProfileCreate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = CustomTargetingProfileRepository(session, tenant_id)
        if repo.get_by_id(payload.custom_targeting_profile_id) is not None:
            return _api_error(
                "conflict",
                f"Custom targeting profile {payload.custom_targeting_profile_id!r} already exists.",
                409,
            )
        profile = CustomTargetingProfile(
            tenant_id=tenant_id,
            custom_targeting_profile_id=payload.custom_targeting_profile_id,
            name=payload.name,
            description=payload.description,
            components=payload.components.model_dump(),
            adapter_config={},
            touches_dimensions=payload.touches_dimensions,
        )
        repo.add(profile)
        session.flush()
        _refresh_custom_targeting_profile_etag(profile)
        session.commit()
        body = _custom_targeting_profile_to_read(profile)
        return jsonify(body), 201, {"ETag": f'"{profile.etag}"'}


@composition_api.route(
    "/tenants/<tenant_id>/custom-targeting-profiles/<profile_id>",
    methods=["PUT"],
)
@require_composition_api_key
def update_custom_targeting_profile(tenant_id: str, profile_id: str):
    try:
        payload = CustomTargetingProfileUpdate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = CustomTargetingProfileRepository(session, tenant_id)
        profile = repo.get_by_id(profile_id)
        if profile is None:
            return _api_error(
                "custom_targeting_profile_not_found",
                f"Custom targeting profile {profile_id!r} not found.",
                404,
            )
        if payload.name is not None:
            profile.name = payload.name
        if payload.description is not None:
            profile.description = payload.description
        if payload.components is not None:
            profile.components = payload.components.model_dump()
        if payload.touches_dimensions is not None:
            profile.touches_dimensions = payload.touches_dimensions
        _refresh_custom_targeting_profile_etag(profile)
        session.commit()
        return (
            jsonify(_custom_targeting_profile_to_read(profile)),
            200,
            {"ETag": f'"{profile.etag}"'},
        )


@composition_api.route(
    "/tenants/<tenant_id>/custom-targeting-profiles/<profile_id>",
    methods=["DELETE"],
)
@require_composition_api_key
def delete_custom_targeting_profile(tenant_id: str, profile_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = CustomTargetingProfileRepository(session, tenant_id)
        profile = repo.get_by_id(profile_id)
        if profile is None:
            return _api_error(
                "custom_targeting_profile_not_found",
                f"Custom targeting profile {profile_id!r} not found.",
                404,
            )
        repo.delete(profile)
        session.commit()
        return "", 204


# ---------------------------------------------------------------------------
# Embedded principal — lazy auto-resolution
# ---------------------------------------------------------------------------


# Reserved external_id marker for the per-tenant embedded principal. The
# host (storefront) is the only agent in embedded mode; this principal
# exists so composed Products can be scoped to a principal_id for
# get_products filtering and AuditLog consistency.
_EMBEDDED_PRINCIPAL_EXTERNAL_ID = "__embedded_host__"


def _resolve_or_create_embedded_principal(session, tenant: Tenant) -> Principal:
    """Return the tenant's embedded principal, creating it on first call.

    The embedded principal has NULL ``access_token`` (no direct-to-/mcp
    auth path in embedded mode). The ``platform_mappings`` entry is a
    sentinel under the tenant's ad-server key — actual GAM (or other
    adapter) advertiser resolution flows through advertiser routing rules,
    not through this row. The key just satisfies the
    ``PlatformMappingModel`` validator which requires at least one known
    adapter key.
    """
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
        # Race: a concurrent compose claimed the slot. Re-read.
        session.rollback()
        replay = repo.get_by_external_id(_EMBEDDED_PRINCIPAL_EXTERNAL_ID)
        if replay is None:
            raise
        return replay
    return principal


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _load_custom_targeting_keys(session, tenant_id: str) -> dict[str, Any]:
    """Operator's custom-targeting-key taxonomy lives on AdapterConfig as a
    name→GAM-id map. Returns the raw mapping (empty dict if no adapter config)."""
    config = session.get(AdapterConfig, tenant_id)
    if config is None:
        return {}
    return dict(getattr(config, "custom_targeting_keys", {}) or {})


@composition_api.route("/tenants/<tenant_id>/custom-targeting-keys", methods=["GET"])
@require_composition_api_key
def list_custom_targeting_keys(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        mapping = _load_custom_targeting_keys(session, tenant_id)
        items = [{"key": key} for key in sorted(mapping.keys())]
        body = CustomTargetingKeysResponse.model_validate({"custom_targeting_keys": items}).model_dump(mode="json")
        return jsonify(body), 200


@composition_api.route(
    "/tenants/<tenant_id>/custom-targeting-keys/<key>/values",
    methods=["GET"],
)
@require_composition_api_key
def list_custom_targeting_key_values(tenant_id: str, key: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        mapping = _load_custom_targeting_keys(session, tenant_id)
        if key not in mapping:
            return _api_error(
                "custom_targeting_key_not_found",
                f"Custom targeting key {key!r} not found.",
                404,
            )
        # The adapter taxonomy stores key→id; per-value enumeration requires
        # an adapter round-trip (e.g. GAM CustomTargetingService) which lands
        # in a later phase. v1 returns the key but no values until adapter
        # discovery is wired in.
        body = CustomTargetingKeyValuesResponse.model_validate({"key": key, "values": []}).model_dump(mode="json")
        return jsonify(body), 200


@composition_api.route("/tenants/<tenant_id>/audience-segments", methods=["GET"])
@require_composition_api_key
def list_audience_segments(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        # Adapter-provided audience segments require an adapter round-trip
        # (GAM AudienceSegmentService) which lands in a later phase. v1
        # returns an empty list — the endpoint exists so storefront clients
        # can wire their UI now and pick up live data later without a
        # contract change.
        body = AudienceSegmentsResponse.model_validate({"audience_segments": []}).model_dump(mode="json")
        return jsonify(body), 200


# ---------------------------------------------------------------------------
# Dynamic product composition
# ---------------------------------------------------------------------------


DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # fallback if neither request nor tenant set one


def _compute_expires_at(
    *,
    flight_start: date,
    requested_ttl_seconds: int | None,
    tenant_max_ttl_seconds: int | None,
) -> datetime:
    """``min(flight_start, created_at + tenant_max_ttl, created_at + requested_ttl)``.

    Flight-start cap protects against indefinite price commitments; tenant max
    is the operator's policy upper bound; requested is the storefront's hint.
    """
    now = datetime.now(UTC)
    candidates: list[datetime] = []

    flight_start_dt = datetime.combine(flight_start, datetime.min.time(), tzinfo=UTC)
    candidates.append(flight_start_dt)

    if tenant_max_ttl_seconds and tenant_max_ttl_seconds > 0:
        candidates.append(now + timedelta(seconds=tenant_max_ttl_seconds))

    if requested_ttl_seconds and requested_ttl_seconds > 0:
        candidates.append(now + timedelta(seconds=requested_ttl_seconds))

    if len(candidates) == 1:
        # only the flight-start cap available; fall back to default TTL too
        candidates.append(now + timedelta(seconds=DEFAULT_TTL_SECONDS))

    return min(candidates)


def _materialize_implementation_config(
    *,
    inventory_profile: InventoryProfile,
    custom_targeting_profiles: list[CustomTargetingProfile],
) -> dict:
    """Merge inventory profile inventory_config + each profile's adapter_config
    into a single implementation_config the adapter layer can consume directly.

    Schema (additive over the GAM-shaped baseline used by
    ``Product.effective_implementation_config``):

      {
        "targeted_ad_unit_ids": [...],
        "targeted_placement_ids": [...],
        "include_descendants": bool,
        "custom_targeting": {key: [values]},      # merged from profiles
        "excluded_custom_targeting": {key: [...]},
        "audience_segment_ids": [...],
        "excluded_audience_segment_ids": [...],
      }
    """
    inv = inventory_profile.inventory_config or {}
    config: dict[str, Any] = {
        "targeted_ad_unit_ids": list(inv.get("ad_units", []) or []),
        "targeted_placement_ids": list(inv.get("placements", []) or []),
        "include_descendants": bool(inv.get("include_descendants", True)),
    }

    custom_include: dict[str, list[str]] = {}
    custom_exclude: dict[str, list[str]] = {}
    aud_include: list[str] = []
    aud_exclude: list[str] = []

    for profile in custom_targeting_profiles:
        components = profile.components or {}
        for kv in components.get("key_values", []) or []:
            target = custom_include if kv.get("mode", "include") == "include" else custom_exclude
            target.setdefault(kv["key"], []).extend(kv.get("values", []))
        for seg in components.get("audience_segments", []) or []:
            target = aud_include if seg.get("mode", "include") == "include" else aud_exclude
            target.append(seg["segment_id"])

    if custom_include:
        config["custom_targeting"] = custom_include
    if custom_exclude:
        config["excluded_custom_targeting"] = custom_exclude
    if aud_include:
        config["audience_segment_ids"] = aud_include
    if aud_exclude:
        config["excluded_audience_segment_ids"] = aud_exclude

    return config


def _validate_capability_narrowing(
    *,
    inventory_profile: InventoryProfile,
    custom_targeting_profiles: list[CustomTargetingProfile],
) -> list[CompositionErrorDetail]:
    """Cross-check that each custom targeting profile's ``touches_dimensions``
    is a subset of the inventory profile's allowed ``constraints.targeting_dimensions``.

    Best-effort: only runs when both sides declare constraints. Missing
    constraints metadata is treated as "no narrowing claimed" and skipped —
    operators can adopt the typed constraints gradually.
    """
    errors: list[CompositionErrorDetail] = []
    inv_constraints = inventory_profile.constraints or {}
    allowed = inv_constraints.get("targeting_dimensions")
    if not allowed:
        return errors
    allowed_set = set(allowed)
    for profile in custom_targeting_profiles:
        touches = profile.touches_dimensions or []
        for dim in touches:
            if dim not in allowed_set:
                errors.append(
                    CompositionErrorDetail(
                        code="dimension_unsupported",
                        inventory_profile_id=inventory_profile.profile_id,
                        custom_targeting_profile_id=profile.custom_targeting_profile_id,
                        dimension=dim,
                        message=f"Inventory profile does not support targeting dimension {dim!r}.",
                    )
                )
    return errors


# Supported pricing models — mirrors core/main.py build_router declaration.
# Storefront-composed products may only use a pricing_model the agent's
# DecisioningCapabilities declared support for. Per-tenant override could
# come later when adapters declare distinct capability sets.
SUPPORTED_PRICING_MODELS: frozenset[str] = frozenset({"cpm"})


def _persist_pricing_option(
    *,
    session,
    tenant_id: str,
    product_id: str,
    pricing_option,
) -> PricingOption:
    """Translate an AdCP PricingOption into the ORM row that ``Product.pricing_options``
    expects. The full AdCP shape is also cached on ``parameters`` so the read
    path is lossless even though the ORM column model is flatter than the
    spec.
    """
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
    """Return the AdCP PricingOption dict cached on the row. Falls back to a
    minimal reconstruction for rows that didn't come through this API path."""
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


def _dynamic_product_to_read(product: Product, pricing_option_dict: dict) -> dict:
    flight_start = (product.expires_at and product.expires_at.date()) or date.today()
    return DynamicProductRead(
        product_id=product.product_id,
        composition_source="storefront_composed",
        inventory_profile_id=(product.inventory_profile.profile_id if product.inventory_profile else ""),
        custom_targeting_profile_ids=list(product.custom_targeting_profile_ids or []),
        pricing_option=pricing_option_dict,
        flight_start=flight_start,
        flight_end=flight_start,
        expires_at=product.expires_at or datetime.now(UTC),
        implementation_config_summary=product.implementation_config or {},
        created_at=getattr(product, "created_at", datetime.now(UTC)),
    ).model_dump(mode="json")


@composition_api.route("/tenants/<tenant_id>/products", methods=["POST"])
@require_composition_api_key
def compose_product(tenant_id: str):
    """Materialize a dynamic product from primitives. Idempotency-Key header required.

    The storefront supplies a full ``PricingOption`` (AdCP's typed wire shape);
    the sales agent records it as a ``Product.pricing_options`` row and returns
    it lossless on read. No agent identification needed — in embedded mode the
    host is the only agent, auto-resolved on first compose.
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
                                f"pricing_model={payload.pricing_option.pricing_model!r} is not in the "
                                f"agent's declared supported_pricing_models."
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

        # Auto-resolve the embedded principal — no buyer-side principal API.
        embedded_principal = _resolve_or_create_embedded_principal(session, tenant)

        products = ProductRepository(session, tenant_id)

        # Idempotency replay — return the previously composed product unchanged.
        existing = products.find_composed_by_idempotency_key(
            principal_id=embedded_principal.principal_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            existing_pricing = _read_pricing_option(existing.pricing_options[0]) if existing.pricing_options else {}
            return jsonify(_dynamic_product_to_read(existing, existing_pricing)), 200

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

        ctp_repo = CustomTargetingProfileRepository(session, tenant_id)
        custom_targeting_profiles = (
            ctp_repo.list_by_ids(payload.custom_targeting_profile_ids) if payload.custom_targeting_profile_ids else []
        )
        if len(custom_targeting_profiles) != len(payload.custom_targeting_profile_ids):
            found = {p.custom_targeting_profile_id for p in custom_targeting_profiles}
            missing = [pid for pid in payload.custom_targeting_profile_ids if pid not in found]
            return (
                jsonify(
                    CompositionError(
                        details=[
                            CompositionErrorDetail(
                                code="unknown_custom_targeting_profile",
                                custom_targeting_profile_id=pid,
                                message=f"Custom targeting profile {pid!r} not found.",
                            )
                            for pid in missing
                        ]
                    ).model_dump(mode="json")
                ),
                422,
            )

        narrowing_errors = _validate_capability_narrowing(
            inventory_profile=inventory_profile,
            custom_targeting_profiles=custom_targeting_profiles,
        )
        if narrowing_errors:
            return (
                jsonify(CompositionError(details=narrowing_errors).model_dump(mode="json")),
                422,
            )

        implementation_config = _materialize_implementation_config(
            inventory_profile=inventory_profile,
            custom_targeting_profiles=custom_targeting_profiles,
        )

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
            # XOR constraint: must set exactly one of properties / property_tags.
            # The effective publisher properties come via the inventory profile;
            # the Product row carries a placeholder tag so the DB-level XOR check passes.
            property_tags=["all_inventory"],
            expires_at=expires_at,
            composition_source="storefront_composed",
            composed_by_principal_id=embedded_principal.principal_id,
            idempotency_key=idempotency_key,
            custom_targeting_profile_ids=list(payload.custom_targeting_profile_ids),
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
            # Race: another writer claimed this (principal, idempotency_key)
            # between our replay probe and commit. Re-read and serve the
            # winning row so the storefront still gets a consistent result.
            session.rollback()
            replay = products.find_composed_by_idempotency_key(
                principal_id=embedded_principal.principal_id,
                idempotency_key=idempotency_key,
            )
            if replay is not None:
                replay_pricing = _read_pricing_option(replay.pricing_options[0]) if replay.pricing_options else {}
                return jsonify(_dynamic_product_to_read(replay, replay_pricing)), 200
            return _api_error("conflict", str(exc), 409)

        # Snapshot before exiting the session (audit-logger opens its own
        # session; lazy-loads after that can detach).
        pricing_option_dict = payload.pricing_option.model_dump(mode="json")
        response_body = _dynamic_product_to_read(product, pricing_option_dict)
        audit_details = {
            "product_id": product.product_id,
            "inventory_profile_id": inventory_profile.profile_id,
            "custom_targeting_profile_ids": list(payload.custom_targeting_profile_ids),
            "pricing_option": pricing_option_dict,
            "idempotency_key": idempotency_key,
            "expires_at": expires_at.isoformat(),
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
        # Audit-log failures must not fail the operation. The audit logger
        # already falls back to file logging on DB errors.
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
        return jsonify(_dynamic_product_to_read(product, pricing_option_dict)), 200


# ---------------------------------------------------------------------------
# Advertiser mappings (AccountReference → adapter advertiser)
# ---------------------------------------------------------------------------


def _account_from_rule(rule: AdvertiserRoutingRule) -> dict:
    """Translate an ``AdvertiserRoutingRule``'s internal columns into the
    :class:`AccountPattern` wire shape. NULL ``brand_house``/``brand_id``
    surface as omitted fields so the wildcard semantics survive the round trip."""
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
    """Pull (operator_domain, brand_house, brand_id) from an AccountPattern.

    The strict AdCP ``AccountReference`` is a discriminated union (variant 1:
    account_id, variant 2: operator + brand + sandbox). Routing rules use a
    looser :class:`AccountPattern` where ``brand`` and its components may be
    omitted to express wildcards — same vocabulary, additional flexibility.
    """
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
        # The natural key uniqueness check is enforced at DB level via the
        # uq_routing_rule_natural_key index; this lookup short-circuits the
        # happy path so callers see a clean 409 instead of an IntegrityError.
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
            principal_id=None,  # embedded mode: no per-agent narrowing
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
    """Only ``adapter_advertiser_id`` is mutable. Changing the natural key
    requires DELETE + POST so the uniqueness index can't be silently violated."""
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
    """Synced cache of adapter advertisers for the picker. Read-only — the
    sync job hydrates this from the adapter (GAM ``CompanyService``)."""
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
