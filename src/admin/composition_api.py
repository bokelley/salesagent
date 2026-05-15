"""Embedded Composition API — REST blueprint at ``/api/v1/tenants/<tenant_id>/...``.

Server-to-server surface for an embedding storefront (e.g. Scope3) to manage
the primitives it composes products from: inventory profiles, custom targeting
profiles, principals, and (in a follow-up phase) dynamic product creation.

Auth: same operator/wrapper API key as the Tenant Management API
(``X-Tenant-Management-API-Key``). No new MCP tools — this is REST only.

Pricing: storefront sets ``agreed_cpm`` end-to-end. The sales agent records
it for audit; no floor enforcement.

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
    PrincipalCreate,
    PrincipalCreated,
    PrincipalListResponse,
    PrincipalRead,
    PrincipalUpdate,
    TargetingComponents,
    TokenRotatedResponse,
)
from src.admin.auth_helpers import require_api_key_auth
from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    CustomTargetingProfile,
    InventoryProfile,
    Principal,
    Product,
    Tenant,
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
# Principals
# ---------------------------------------------------------------------------


def _principal_to_read(principal: Principal) -> dict:
    return PrincipalRead(
        principal_id=principal.principal_id,
        external_id=principal.external_id,
        name=principal.name,
        platform_mappings=principal.platform_mappings or {},
        agent_url=principal.agent_url,
        brand_domain=principal.brand_domain,
        billing_enabled=bool(principal.billing_enabled),
        created_at=principal.created_at,
        updated_at=principal.updated_at,
    ).model_dump(mode="json")


@composition_api.route("/tenants/<tenant_id>/principals", methods=["GET"])
@require_composition_api_key
def list_principals(tenant_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = PrincipalRepository(session, tenant_id)
        principals = repo.list_all()
        items = [_principal_to_read(p) for p in principals]
        body = PrincipalListResponse(principals=items).model_dump(mode="json")
        return jsonify(body), 200


@composition_api.route("/tenants/<tenant_id>/principals/<principal_id>", methods=["GET"])
@require_composition_api_key
def get_principal(tenant_id: str, principal_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = PrincipalRepository(session, tenant_id)
        principal = repo.get_by_id(principal_id)
        if principal is None:
            return _api_error(
                "principal_not_found",
                f"Principal {principal_id!r} not found.",
                404,
            )
        return jsonify(_principal_to_read(principal)), 200


@composition_api.route("/tenants/<tenant_id>/principals", methods=["POST"])
@require_composition_api_key
def create_principal(tenant_id: str):
    """Idempotent on (tenant, external_id). Repeat POST returns the existing row
    without rotating the token."""
    try:
        payload = PrincipalCreate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = PrincipalRepository(session, tenant_id)
        existing = repo.get_by_external_id(payload.external_id)
        if existing is not None:
            # Idempotent replay — token is NOT re-emitted (security: only on create).
            body = _principal_to_read(existing)
            return jsonify(body), 200

        principal_id = f"principal_{secrets.token_hex(8)}"
        access_token = secrets.token_urlsafe(32)
        principal = Principal(
            tenant_id=tenant_id,
            principal_id=principal_id,
            external_id=payload.external_id,
            name=payload.name,
            platform_mappings=payload.platform_mappings,
            access_token=access_token,
            agent_url=payload.agent_url,
            brand_domain=payload.brand_domain,
        )
        repo.add(principal)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            # Race: another writer claimed this external_id between our
            # idempotency probe and commit. Re-read and treat as replay.
            replay = repo.get_by_external_id(payload.external_id)
            if replay is not None:
                return jsonify(_principal_to_read(replay)), 200
            return _api_error("conflict", str(exc), 409)

        body = PrincipalCreated(
            **_principal_to_read(principal),
            access_token=access_token,
        ).model_dump(mode="json")
        return jsonify(body), 201


@composition_api.route("/tenants/<tenant_id>/principals/<principal_id>", methods=["PUT"])
@require_composition_api_key
def update_principal(tenant_id: str, principal_id: str):
    try:
        payload = PrincipalUpdate.model_validate(request.get_json() or {})
    except Exception as exc:
        return _api_error("invalid_request", str(exc), 400)

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = PrincipalRepository(session, tenant_id)
        principal = repo.get_by_id(principal_id)
        if principal is None:
            return _api_error(
                "principal_not_found",
                f"Principal {principal_id!r} not found.",
                404,
            )
        if payload.name is not None:
            principal.name = payload.name
        if payload.platform_mappings is not None:
            principal.platform_mappings = payload.platform_mappings
        if payload.agent_url is not None:
            principal.agent_url = payload.agent_url
        if payload.brand_domain is not None:
            principal.brand_domain = payload.brand_domain
        if payload.billing_enabled is not None:
            principal.billing_enabled = payload.billing_enabled
        session.commit()
        return jsonify(_principal_to_read(principal)), 200


@composition_api.route("/tenants/<tenant_id>/principals/<principal_id>", methods=["DELETE"])
@require_composition_api_key
def delete_principal(tenant_id: str, principal_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = PrincipalRepository(session, tenant_id)
        principal = repo.get_by_id(principal_id)
        if principal is None:
            return _api_error(
                "principal_not_found",
                f"Principal {principal_id!r} not found.",
                404,
            )
        repo.delete(principal)
        session.commit()
        return "", 204


@composition_api.route(
    "/tenants/<tenant_id>/principals/<principal_id>/rotate-token",
    methods=["POST"],
)
@require_composition_api_key
def rotate_principal_token(tenant_id: str, principal_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)
        repo = PrincipalRepository(session, tenant_id)
        principal = repo.get_by_id(principal_id)
        if principal is None:
            return _api_error(
                "principal_not_found",
                f"Principal {principal_id!r} not found.",
                404,
            )
        new_token = secrets.token_urlsafe(32)
        principal.access_token = new_token
        session.commit()
        body = TokenRotatedResponse(principal_id=principal_id, access_token=new_token).model_dump(mode="json")
        return jsonify(body), 200


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


def _dynamic_product_to_read(product: Product) -> dict:
    flight_start = (product.expires_at and product.expires_at.date()) or None
    # Fall back to today if we can't infer (shouldn't happen for valid
    # dynamic products, but keep the typed response shape stable).
    return DynamicProductRead(
        product_id=product.product_id,
        composition_source="storefront_composed",
        inventory_profile_id=(product.inventory_profile.profile_id if product.inventory_profile else ""),
        custom_targeting_profile_ids=list(product.custom_targeting_profile_ids or []),
        agreed_cpm=product.agreed_cpm or Decimal("0"),
        flight_start=flight_start or date.today(),
        flight_end=flight_start or date.today(),
        principal_id=product.composed_by_principal_id or "",
        expires_at=product.expires_at or datetime.now(UTC),
        implementation_config_summary=product.implementation_config or {},
        created_at=getattr(product, "created_at", datetime.now(UTC)),
    ).model_dump(mode="json")


@composition_api.route("/tenants/<tenant_id>/products", methods=["POST"])
@require_composition_api_key
def compose_product(tenant_id: str):
    """Materialize a dynamic product from primitives. Idempotency-Key header required."""
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

    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} not found.", 404)

        principals = PrincipalRepository(session, tenant_id)
        principal = principals.get_by_id(payload.principal_id)
        if principal is None:
            return (
                jsonify(
                    CompositionError(
                        details=[
                            CompositionErrorDetail(
                                code="missing_principal",
                                principal_id=payload.principal_id,
                                message=f"Principal {payload.principal_id!r} not found.",
                            )
                        ]
                    ).model_dump(mode="json")
                ),
                422,
            )

        products = ProductRepository(session, tenant_id)

        # Idempotency replay — return the previously composed product unchanged.
        existing = products.find_composed_by_idempotency_key(
            principal_id=payload.principal_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return jsonify(_dynamic_product_to_read(existing)), 200

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
            name=f"Composed for {principal.name}",
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
            composed_by_principal_id=principal.principal_id,
            idempotency_key=idempotency_key,
            custom_targeting_profile_ids=list(payload.custom_targeting_profile_ids),
            agreed_cpm=payload.agreed_cpm,
            allowed_principal_ids=[principal.principal_id],
        )
        products.create(product)
        try:
            session.commit()
        except IntegrityError as exc:
            # Race: another writer claimed this (principal, idempotency_key)
            # between our replay probe and commit. Re-read and serve the
            # winning row so the storefront still gets a consistent result.
            session.rollback()
            replay = products.find_composed_by_idempotency_key(
                principal_id=payload.principal_id,
                idempotency_key=idempotency_key,
            )
            if replay is not None:
                return jsonify(_dynamic_product_to_read(replay)), 200
            return _api_error("conflict", str(exc), 409)

        # Materialize the response body BEFORE the audit-log call. The audit
        # logger opens its own get_db_session() context; depending on the
        # session-scope strategy that can detach this session's instances
        # from their bindings, which then breaks lazy-load on attribute
        # access. Snapshotting the response payload first avoids that hazard
        # entirely and lets us treat audit emission as best-effort.
        response_body = _dynamic_product_to_read(product)
        audit_details = {
            "product_id": product.product_id,
            "inventory_profile_id": inventory_profile.profile_id,
            "custom_targeting_profile_ids": list(payload.custom_targeting_profile_ids),
            "agreed_cpm": str(payload.agreed_cpm),
            "idempotency_key": idempotency_key,
            "expires_at": expires_at.isoformat(),
        }
        principal_name = principal.name
        principal_id_value = principal.principal_id

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
        product = products.get_by_id(product_id)
        if product is None or product.composition_source != "storefront_composed":
            return _api_error(
                "product_not_found",
                f"Composed product {product_id!r} not found.",
                404,
            )
        return jsonify(_dynamic_product_to_read(product)), 200
