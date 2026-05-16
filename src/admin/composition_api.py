"""Embedded Composition API — REST blueprint at ``/api/v1/tenants/<tenant_id>/...``.

Server-to-server surface for an embedding storefront (e.g. Scope3) to author
the primitives an AdCP buyer composes against: inventory profiles, tenant
signals (operator's map of adapter targeting capabilities), and advertiser
mappings.

Auth: same operator/wrapper API key as the Tenant Management API
(``X-Tenant-Management-API-Key``). No new MCP tools — REST only.

AdCP-pure composition: discovery and composition happen through the
standard AdCP tools (``get_products``, ``get_signals``, ``create_media_buy``)
— not through a parallel "compose product" surface. The storefront treats
us like any other AdCP agent:

- Operator authors a non-guaranteed ``Product`` with a floor-priced
  ``PricingOption`` and an ``InventoryProfile`` — this is the wholesale /
  composable product. It shows up in ``get_products`` like any other
  catalog entry.
- Operator authors ``TenantSignal`` rows declaring adapter capabilities
  (custom KVs, audiences, …). These surface through the AdCP
  ``get_signals`` tool — same wire shape as third-party signals agents.
- Storefront composes by building a ``CreateMediaBuyRequest``: picks a
  wholesale product, layers signals in ``PackageRequest.targeting_overlay``,
  layers optimization in ``optimization_goals`` / ``performance_standards``
  / ``pacing`` / ``bid_price``, sets the agreed price in ``budget`` /
  ``bid_price`` against the product's ``PricingOption``.
- This blueprint is OPERATOR-AUTHORING ONLY. Storefront discovery flows
  through AdCP tools; storefront purchase flows through
  ``create_media_buy``. No separate compose write.

In embedded mode the host (storefront) is the only agent. The sales
agent never receives requests directly from a buyer.

See ``.context/embedded-composition-design.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime
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
    InventoryProfileCreate,
    InventoryProfileListResponse,
    InventoryProfileRead,
    InventoryProfileUpdate,
    SignalRange,
    TenantSignalCreate,
    TenantSignalListResponse,
    TenantSignalRead,
    TenantSignalUpdate,
)
from src.admin.auth_helpers import require_api_key_auth
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdvertiserRoutingRule,
    InventoryProfile,
    Tenant,
    TenantSignal,
)
from src.core.database.repositories.advertiser_mapping import (
    AdvertiserMappingRepository,
    GamAdvertiserRepository,
)
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
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
# Inventory profiles (operator authoring)
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
# Tenant signals (operator-authored capability map)
#
# Storefront DISCOVERY flows through the AdCP ``get_signals`` tool (wired
# elsewhere to read ``tenant_signals`` + any registered external
# ``SignalsAgent`` rows). The endpoints below are operator-authoring —
# they include ``adapter_config`` (opaque to storefront) on Create/Update.
# GET surfaces here are storefront-friendly (no adapter_config echo) but
# operators may use them for inspection.
# ---------------------------------------------------------------------------


def _signal_range(signal: TenantSignal) -> SignalRange | None:
    if signal.range_min is None and signal.range_max is None:
        return None
    return SignalRange(min=signal.range_min, max=signal.range_max)


def _signal_to_read(signal: TenantSignal) -> dict:
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
