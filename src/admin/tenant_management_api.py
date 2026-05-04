"""Tenant Management API for managing tenants.

Sprint 1 of [managed-tenant-mode](../../docs/design/managed-tenant-mode.md)
extends this blueprint with spectree-validated endpoints for the platform-managed
surface (provision / list / get / patch / deactivate / reactivate / delete /
adapter-config / adapter-config test). Legacy non-spectree endpoints below
remain for direct-customer (open-instance) callers.
"""

import json
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request
from spectree import Response, SpecTree
from sqlalchemy import delete, func, select

from src.admin.api_schemas.tenant_management import (
    AdapterConfig as AdapterConfigSchema,
)
from src.admin.api_schemas.tenant_management import (
    AdapterConfigResponse,
    AdapterStatusResponse,
    ApiError,
    GAMAdapterConfig,
    ListTenantsResponse,
    MockAdapterConfig,
    PreviewAdapterRequest,
    PreviewAdapterResponse,
    ProvisionedPrincipalResponse,
    ProvisionTenantRequest,
    ProvisionTenantResponse,
    TenantDetail,
    TenantStatusResponse,
    TenantSummary,
    TestConnectionResponse,
    UpdateTenantRequest,
)
from src.admin.auth_helpers import require_api_key_auth
from src.admin.services.adapter_connection_tester import preview_adapter, test_adapter_connection
from src.admin.services.tenant_status_service import get_tenant_status, invalidate_status_cache
from src.core.database.database_session import get_db_session
from src.core.database.managed_tenant_guard import ManagedTenantWriteError
from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    MediaBuy,
    Principal,
    PropertyTag,
    Tenant,
)

logger = logging.getLogger(__name__)

# Create Blueprint
tenant_management_api = Blueprint("tenant_management_api", __name__, url_prefix="/api/v1/tenant-management")

# OpenAPI spec — Swagger UI at /api/v1/tenant-management/docs, spec at /api/v1/tenant-management/openapi.json
spec = SpecTree(
    "flask",
    title="Sales Agent — Tenant Management API",
    version="v1",
    path="docs",
    openapi_url_prefix="",
)


require_tenant_management_api_key = require_api_key_auth(
    env_var="TENANT_MANAGEMENT_API_KEY",
    config_key="tenant_management_api_key",
    header="X-Tenant-Management-API-Key",
)


# ---------------------------------------------------------------------------
# Helpers shared by the new spectree endpoints
# ---------------------------------------------------------------------------


def _api_error(code: str, message: str, status: int, details: dict | None = None):
    """Build a (jsonified, status) tuple matching the :class:`ApiError` schema."""
    body = ApiError(error=code, message=message, details=details).model_dump(exclude_none=True)
    return jsonify(body), status


def _tenant_to_summary(tenant: Tenant, adapter_configured: bool) -> dict:
    """Serialize a :class:`Tenant` as a :class:`TenantSummary`-compatible dict."""
    return TenantSummary(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        subdomain=tenant.subdomain,
        external_org_id=tenant.external_org_id,
        external_source=tenant.external_source,
        managed_externally=bool(tenant.managed_externally),
        is_active=bool(tenant.is_active),
        billing_plan=tenant.billing_plan or "standard",
        ad_server=tenant.ad_server,
        adapter_configured=adapter_configured,
        created_at=tenant.created_at,
    ).model_dump(mode="json")


def _tenant_to_detail(tenant: Tenant, adapter_configured: bool) -> dict:
    """Serialize a :class:`Tenant` as a :class:`TenantDetail`-compatible dict."""
    contact_email = tenant.billing_contact if tenant.billing_contact and "@" in (tenant.billing_contact or "") else None
    default_currency = _resolve_default_currency(tenant.tenant_id)
    return TenantDetail(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        subdomain=tenant.subdomain,
        external_org_id=tenant.external_org_id,
        external_source=tenant.external_source,
        managed_externally=bool(tenant.managed_externally),
        is_active=bool(tenant.is_active),
        billing_plan=tenant.billing_plan or "standard",
        ad_server=tenant.ad_server,
        adapter_configured=adapter_configured,
        created_at=tenant.created_at,
        contact_email=contact_email,
        default_currency=default_currency,
    ).model_dump(mode="json")


def _resolve_default_currency(tenant_id: str) -> str | None:
    """Return the default currency for a tenant, or None if no currency limits exist."""
    with get_db_session() as session:
        stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
        first = session.scalars(stmt).first()
        return first.currency_code if first else None


def _adapter_config_to_dict(adapter: AdapterConfigSchema) -> dict:
    """Flatten the discriminated AdapterConfig into a dict for adapter test/persistence."""
    if isinstance(adapter, GAMAdapterConfig):
        return {
            "type": "google_ad_manager",
            "network_code": adapter.network_code,
            "service_account_email": adapter.service_account_email,
            "service_account_json": adapter.service_account_key_json.get_secret_value(),
            "refresh_token": adapter.refresh_token.get_secret_value() if adapter.refresh_token else None,
        }
    if isinstance(adapter, MockAdapterConfig):
        return {"type": "mock", "dry_run": adapter.dry_run}
    raise ValueError(f"Unsupported adapter type: {type(adapter).__name__}")


def _persist_adapter_config(session, tenant_id: str, adapter: AdapterConfigSchema) -> AdapterConfig:
    """Create or replace the AdapterConfig row for a tenant from a validated schema."""
    stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
    existing = session.scalars(stmt).first()
    if existing is not None:
        session.delete(existing)
        session.flush()

    if isinstance(adapter, GAMAdapterConfig):
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            gam_network_code=adapter.network_code,
            gam_service_account_email=adapter.service_account_email,
            gam_refresh_token=adapter.refresh_token.get_secret_value() if adapter.refresh_token else None,
        )
        # Encryption is wired via the property setter (see models.py:AdapterConfig).
        ac.gam_service_account_json = adapter.service_account_key_json.get_secret_value()
    else:  # MockAdapterConfig
        ac = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="mock",
            mock_dry_run=adapter.dry_run,
        )
    session.add(ac)
    return ac


def _build_adapter_config_response(adapter: AdapterConfig | None) -> AdapterConfigResponse:
    """Build the redacted :class:`AdapterConfigResponse` from a stored row."""
    if adapter is None:
        return AdapterConfigResponse(type="none", configured=False)
    if adapter.adapter_type == "google_ad_manager":
        return AdapterConfigResponse(
            type="google_ad_manager",
            configured=True,
            network_code=adapter.gam_network_code,
            service_account_email=adapter.gam_service_account_email,
            service_account_key_json="<encrypted>" if adapter._gam_service_account_json else None,
            refresh_token="<redacted>" if adapter.gam_refresh_token else None,
        )
    return AdapterConfigResponse(type=adapter.adapter_type, configured=True)


def _surface_urls(tenant_id: str) -> tuple[str, str, str]:
    """Return ``(mcp_url, a2a_url, admin_url_path)`` for a tenant.

    Only the path component is stable in v1 — the host comes from the deployment env.
    """
    base = os.environ.get("ADCP_BASE_URL", "").rstrip("/")
    mcp = f"{base}/mcp/" if base else "/mcp/"
    a2a = f"{base}/a2a" if base else "/a2a"
    admin_path = f"/tenant/{tenant_id}"
    return mcp, a2a, admin_path


@tenant_management_api.route("/health", methods=["GET"])
@require_tenant_management_api_key
def health_check():
    """Health check endpoint for the tenant management API."""
    return jsonify({"status": "healthy", "timestamp": datetime.now(UTC).isoformat()})


@tenant_management_api.route("/tenants", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=ListTenantsResponse, HTTP_500=ApiError))
def list_tenants():
    """List tenants. Optional query params: ``managed_externally``, ``is_active``, ``external_source``."""
    managed_filter = request.args.get("managed_externally")
    active_filter = request.args.get("is_active")
    source_filter = request.args.get("external_source")

    def _to_bool(value: str | None) -> bool | None:
        if value is None:
            return None
        return value.lower() in ("true", "1", "yes")

    with get_db_session() as db_session:
        stmt = select(Tenant).order_by(Tenant.created_at.desc())
        managed_bool = _to_bool(managed_filter)
        if managed_bool is not None:
            stmt = stmt.filter(Tenant.managed_externally.is_(managed_bool))
        active_bool = _to_bool(active_filter)
        if active_bool is not None:
            stmt = stmt.filter(Tenant.is_active.is_(active_bool))
        if source_filter:
            stmt = stmt.filter(Tenant.external_source == source_filter)
        tenants = db_session.scalars(stmt).all()

        # Adapter-configured probe via a separate cheap query keeps the main filter simple.
        configured_ids = set(db_session.scalars(select(AdapterConfig.tenant_id)).all())

        summaries = [_tenant_to_summary(t, t.tenant_id in configured_ids) for t in tenants]
        return jsonify({"tenants": summaries, "count": len(summaries)})


@tenant_management_api.route("/tenants", methods=["POST"])
@require_tenant_management_api_key
def create_tenant():
    """Create a new tenant."""

    from src.core.database.models import AdapterConfig

    with get_db_session() as db_session:
        try:
            from src.core.webhook_validator import WebhookURLValidator

            data = request.get_json()

            # Validate required fields
            required_fields = ["name", "subdomain", "ad_server"]
            for field in required_fields:
                if field not in data:
                    return jsonify({"error": f"Missing required field: {field}"}), 400

            # Validate webhook URLs for SSRF protection
            webhook_fields = {
                "slack_webhook_url": "Slack webhook URL",
                "slack_audit_webhook_url": "Slack audit webhook URL",
                "hitl_webhook_url": "HITL webhook URL",
            }
            for field_name, field_label in webhook_fields.items():
                url = data.get(field_name)
                if url:
                    is_valid, error_msg = WebhookURLValidator.validate_webhook_url(url)
                    if not is_valid:
                        return jsonify({"error": f"Invalid {field_label}: {error_msg}"}), 400

            # Generate tenant ID
            tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
            admin_token = secrets.token_urlsafe(32)

            # Handle authorized emails - automatically add creator's email
            email_list = data.get("authorized_emails", [])
            creator_email = data.get("creator_email")
            if creator_email and creator_email not in email_list:
                email_list.append(creator_email)

            domain_list = data.get("authorized_domains", [])

            # Validate access control - prevent tenant lockout
            if not email_list and not domain_list:
                if creator_email:
                    # Auto-add creator as fallback with warning
                    email_list.append(creator_email)
                    logger.warning(
                        f"No access control specified for tenant {data['name']}, auto-adding creator {creator_email}"
                    )
                else:
                    return (
                        jsonify(
                            {
                                "error": "Must specify at least one authorized email or domain. "
                                "Provide 'authorized_emails', 'authorized_domains', or 'creator_email'."
                            }
                        ),
                        400,
                    )

            # Create tenant
            new_tenant = Tenant(
                tenant_id=tenant_id,
                name=data["name"],
                subdomain=data["subdomain"],
                ad_server=data["ad_server"],
                is_active=data.get("is_active", True),
                billing_plan=data.get("billing_plan", "standard"),
                billing_contact=data.get("billing_contact"),
                # Note: max_daily_budget moved to currency_limits table (per models.py line 55)
                enable_axe_signals=data.get("enable_axe_signals", True),
                authorized_emails=json.dumps(email_list),
                authorized_domains=json.dumps(domain_list),
                slack_webhook_url=data.get("slack_webhook_url"),
                slack_audit_webhook_url=data.get("slack_audit_webhook_url"),
                hitl_webhook_url=data.get("hitl_webhook_url"),
                admin_token=admin_token,
                auto_approve_format_ids=json.dumps(data.get("auto_approve_format_ids", ["display_300x250"])),
                human_review_required=data.get("human_review_required", True),
                policy_settings=json.dumps(data.get("policy_settings", {})),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # Set default measurement provider (Publisher Ad Server)
                measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
            )
            db_session.add(new_tenant)

            # Create adapter config
            adapter_type = data["ad_server"]

            # Insert adapter config with appropriate fields based on type
            if adapter_type == "google_ad_manager":
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    gam_network_code=data.get("gam_network_code"),
                    gam_refresh_token=data.get("gam_refresh_token"),
                    gam_trafficker_id=data.get("gam_trafficker_id"),
                    gam_manual_approval_required=data.get("gam_manual_approval_required", False),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings
            elif adapter_type == "kevel":
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    kevel_network_id=data.get("kevel_network_id"),
                    kevel_api_key=data.get("kevel_api_key"),
                    kevel_manual_approval_required=data.get("kevel_manual_approval_required", False),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            elif adapter_type == "triton":
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    triton_station_id=data.get("triton_station_id"),
                    triton_api_key=data.get("triton_api_key"),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            else:  # mock or other
                new_adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    mock_dry_run=data.get("mock_dry_run", False),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

            db_session.add(new_adapter)

            # Create default principal if requested
            principal_token = None
            if data.get("create_default_principal", True):
                principal_id = f"principal_{uuid.uuid4().hex[:8]}"
                principal_token = secrets.token_urlsafe(32)

                # Add a default platform mapping based on the adapter type
                default_mappings = {}
                if adapter_type == "google_ad_manager":
                    # For GAM, add a placeholder advertiser ID
                    default_mappings = {"google_ad_manager": {"advertiser_id": "placeholder"}}
                elif adapter_type == "kevel":
                    default_mappings = {"kevel": {"advertiser_id": "placeholder"}}
                elif adapter_type == "triton":
                    default_mappings = {"triton": {"advertiser_id": "placeholder"}}
                else:
                    # For mock and others
                    default_mappings = {"mock": {"advertiser_id": "default"}}

                new_principal = Principal(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    name=f"{data['name']} Default Principal",
                    platform_mappings=json.dumps(default_mappings),
                    access_token=principal_token,
                    created_at=datetime.now(UTC),
                )
                db_session.add(new_principal)

            db_session.commit()

            result = {
                "tenant_id": tenant_id,
                "name": data["name"],
                "subdomain": data["subdomain"],
                "admin_token": admin_token,
                "admin_ui_url": (
                    f"http://{data['subdomain']}.localhost:{os.environ.get('ADCP_SALES_PORT', '8080')}"
                    f"/admin/tenant/{tenant_id}"
                ),
            }

            if principal_token:
                result["default_principal_token"] = principal_token

            return jsonify(result), 201

        except Exception as e:
            db_session.rollback()
            if "UNIQUE constraint failed: tenants.subdomain" in str(e):
                return jsonify({"error": "Subdomain already exists"}), 409
            logger.error(f"Error creating tenant: {str(e)}")
            return jsonify({"error": "Failed to create tenant"}), 500


@tenant_management_api.route("/tenants/<tenant_id>", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError))
def get_tenant(tenant_id):
    """Return :class:`TenantDetail` for a tenant. 404 if the tenant doesn't exist."""
    with get_db_session() as db_session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter_stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter = db_session.scalars(adapter_stmt).first()
        return jsonify(_tenant_to_detail(tenant, adapter is not None))


@tenant_management_api.route("/tenants/<tenant_id>", methods=["PUT"])
@require_tenant_management_api_key
def update_tenant(tenant_id):
    """Update a tenant."""
    with get_db_session() as db_session:
        try:
            # Check if tenant exists
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            from src.core.webhook_validator import WebhookURLValidator

            data = request.get_json()

            # Validate webhook URLs before updating for SSRF protection
            webhook_fields = {
                "slack_webhook_url": "Slack webhook URL",
                "slack_audit_webhook_url": "Slack audit webhook URL",
                "hitl_webhook_url": "HITL webhook URL",
            }
            for field_name, field_label in webhook_fields.items():
                if field_name in data and data[field_name]:
                    is_valid, error_msg = WebhookURLValidator.validate_webhook_url(data[field_name])
                    if not is_valid:
                        return jsonify({"error": f"Invalid {field_label}: {error_msg}"}), 400

            # Update fields based on provided data
            if "name" in data:
                tenant.name = data["name"]
            if "is_active" in data:
                tenant.is_active = data["is_active"]
            if "billing_plan" in data:
                tenant.billing_plan = data["billing_plan"]
            if "billing_contact" in data:
                tenant.billing_contact = data["billing_contact"]
            # Note: max_daily_budget moved to currency_limits table (per models.py line 55)
            if "enable_axe_signals" in data:
                tenant.enable_axe_signals = data["enable_axe_signals"]
            if "authorized_emails" in data:
                tenant.authorized_emails = json.dumps(data["authorized_emails"])
            if "authorized_domains" in data:
                tenant.authorized_domains = json.dumps(data["authorized_domains"])
            if "slack_webhook_url" in data:
                tenant.slack_webhook_url = data["slack_webhook_url"]
            if "slack_audit_webhook_url" in data:
                tenant.slack_audit_webhook_url = data["slack_audit_webhook_url"]
            if "hitl_webhook_url" in data:
                tenant.hitl_webhook_url = data["hitl_webhook_url"]
            if "auto_approve_format_ids" in data:
                tenant.auto_approve_format_ids = json.dumps(data["auto_approve_format_ids"])
            if "human_review_required" in data:
                tenant.human_review_required = data["human_review_required"]
            if "policy_settings" in data:
                tenant.policy_settings = json.dumps(data["policy_settings"])

            # Always update the updated_at timestamp
            tenant.updated_at = datetime.now(UTC)

            # Update adapter config if provided
            if "adapter_config" in data:
                adapter_data = data["adapter_config"]

                # Get current adapter
                stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
                adapter = db_session.scalars(stmt).first()

                if adapter:
                    if adapter.adapter_type == "google_ad_manager":
                        if "gam_network_code" in adapter_data:
                            adapter.gam_network_code = adapter_data["gam_network_code"]
                        if "gam_refresh_token" in adapter_data:
                            adapter.gam_refresh_token = adapter_data["gam_refresh_token"]
                        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings
                        if "gam_trafficker_id" in adapter_data:
                            adapter.gam_trafficker_id = adapter_data["gam_trafficker_id"]
                        if "gam_manual_approval_required" in adapter_data:
                            adapter.gam_manual_approval_required = adapter_data["gam_manual_approval_required"]

                    elif adapter.adapter_type == "kevel":
                        if "kevel_network_id" in adapter_data:
                            adapter.kevel_network_id = adapter_data["kevel_network_id"]
                        if "kevel_api_key" in adapter_data:
                            adapter.kevel_api_key = adapter_data["kevel_api_key"]
                        if "kevel_manual_approval_required" in adapter_data:
                            adapter.kevel_manual_approval_required = adapter_data["kevel_manual_approval_required"]

                    elif adapter.adapter_type == "triton":
                        if "triton_station_id" in adapter_data:
                            adapter.triton_station_id = adapter_data["triton_station_id"]
                        if "triton_api_key" in adapter_data:
                            adapter.triton_api_key = adapter_data["triton_api_key"]

                    elif adapter.adapter_type == "mock":
                        if "mock_dry_run" in adapter_data:
                            adapter.mock_dry_run = adapter_data["mock_dry_run"]

                    adapter.updated_at = datetime.now(UTC)

            db_session.commit()

            return jsonify(
                {
                    "tenant_id": tenant_id,
                    "name": tenant.name,
                    "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
                }
            )

        except Exception as e:
            db_session.rollback()
            logger.error(f"Error updating tenant {tenant_id}: {str(e)}")
            return jsonify({"error": f"Failed to update tenant: {str(e)}"}), 500


@tenant_management_api.route("/tenants/<tenant_id>", methods=["DELETE"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError, HTTP_409=ApiError, HTTP_400=ApiError))
def delete_tenant(tenant_id):
    """Soft-delete a tenant by default. Hard-delete requires ``?hard=true`` and ``X-Confirm-Delete: yes``.

    Returns 409 ``tenant_has_active_resources`` if the tenant has any active media buys.
    """
    hard = request.args.get("hard", "false").lower() in ("true", "1", "yes")

    with get_db_session() as db_session:
        db_session.info["management_api_caller"] = True

        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        # Active-resources guard fires for both soft and hard delete: a tenant with live buys
        # should not be flipped inactive without an explicit policy decision upstream.
        active_count = db_session.scalar(
            select(func.count())
            .select_from(MediaBuy)
            .where(MediaBuy.tenant_id == tenant_id, MediaBuy.status.in_(("active", "live", "running")))
        )
        if active_count and active_count > 0:
            return _api_error(
                "tenant_has_active_resources",
                f"Tenant {tenant_id!r} has {active_count} active media buys",
                409,
                details={"active_media_buys": int(active_count)},
            )

        if hard:
            confirm = request.headers.get("X-Confirm-Delete", "").lower()
            if confirm != "yes":
                return _api_error(
                    "confirmation_required",
                    "Hard delete requires X-Confirm-Delete: yes header",
                    400,
                )
            tenant_detail = _tenant_to_detail(tenant, adapter_configured=False)
            # Hard delete relies on Tenant's ``cascade="all, delete-orphan"`` relationships
            # for most child tables. PropertyTag uses a backref without a delete cascade,
            # so wipe its rows first via the FK ON DELETE rule. Issuing the bulk delete
            # explicitly avoids the unit-of-work attempting to NULL composite-PK columns.
            db_session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == tenant_id))
            db_session.delete(tenant)
            db_session.commit()
            return jsonify(tenant_detail)

        tenant.is_active = False
        tenant.updated_at = datetime.now(UTC)
        adapter_present = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        try:
            db_session.commit()
        except ManagedTenantWriteError as exc:
            db_session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        return jsonify(_tenant_to_detail(tenant, adapter_present))


# ---------------------------------------------------------------------------
# Sprint 1 endpoints (managed-tenant mode)
# ---------------------------------------------------------------------------


@tenant_management_api.route("/tenants/provision", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=ProvisionTenantRequest,
    resp=Response(
        HTTP_201=ProvisionTenantResponse,
        HTTP_400=ApiError,
        HTTP_409=ApiError,
        HTTP_500=ApiError,
    ),
)
def provision_tenant():
    """Provision a managed tenant (one-shot create + adapter + currency + property tag + optional principal)."""
    req: ProvisionTenantRequest = request.context.json

    # Step 1: external_org_id uniqueness check (informational — not unique at DB level today).
    with get_db_session() as preflight:
        existing = preflight.scalars(
            select(Tenant).filter_by(external_org_id=req.external_org_id, external_source=req.external_source)
        ).first()
        if existing is not None:
            return _api_error(
                "external_org_id_conflict",
                f"external_org_id {req.external_org_id!r} already maps to tenant {existing.tenant_id!r}",
                409,
                details={"tenant_id": existing.tenant_id},
            )

    # Step 2: probe the adapter BEFORE writing anything. A failure here means we never
    # touch the DB at all — keeps the table free of half-configured tenants.
    adapter_dict = _adapter_config_to_dict(req.adapter)
    success, error = test_adapter_connection(adapter_dict["type"], adapter_dict)
    if not success:
        return _api_error(
            "adapter_connection_failed",
            f"Adapter {adapter_dict['type']!r} connection probe failed: {error}",
            400,
            details={"adapter_type": adapter_dict["type"], "error": error},
        )

    # Step 3: open a transaction; create everything in one commit.
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    subdomain_seed = req.external_org_id.lower().replace("_", "-")
    subdomain = f"{subdomain_seed}-{tenant_id[-8:]}"

    initial_principal_id: str | None = None
    initial_principal_name: str | None = None

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        new_tenant = Tenant(
            tenant_id=tenant_id,
            name=req.name,
            subdomain=subdomain,
            ad_server=adapter_dict["type"],
            is_active=True,
            billing_plan=req.billing_plan,
            billing_contact=req.contact_email,
            managed_externally=True,
            external_org_id=req.external_org_id,
            external_source=req.external_source,
            authorized_emails=[req.contact_email],
            authorized_domains=[],
            human_review_required=True,
            auto_approve_format_ids=[],
            measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
        )
        session.add(new_tenant)
        session.flush()

        _persist_adapter_config(session, tenant_id, req.adapter)

        # Default CurrencyLimit (USD or override).
        session.add(
            CurrencyLimit(
                tenant_id=tenant_id,
                currency_code=req.default_currency,
                min_package_budget=None,
                max_daily_package_spend=None,
            )
        )

        # Default PropertyTag — products that don't pin specific properties default to all_inventory.
        session.add(
            PropertyTag(
                tenant_id=tenant_id,
                tag_id="all_inventory",
                name="All Inventory",
                description="Default property tag for all inventory",
            )
        )

        if req.initial_principal is not None:
            initial_principal_id = f"principal_{uuid.uuid4().hex[:8]}"
            initial_principal_name = req.initial_principal.name
            platform_mappings: dict[str, dict] = {}
            if adapter_dict["type"] == "google_ad_manager":
                advertiser = req.initial_principal.external_advertiser_id or "placeholder"
                platform_mappings = {"google_ad_manager": {"advertiser_id": advertiser}}
            elif adapter_dict["type"] == "mock":
                platform_mappings = {
                    "mock": {"advertiser_id": req.initial_principal.external_advertiser_id or "default"}
                }

            # Managed-mode principals don't carry a buyer-protocol token (see sprint 2).
            # We still need a non-null access_token for backward compatibility with non-managed
            # callers that read this column; use a marker prefix so it can never be confused
            # with a real bearer token.
            session.add(
                Principal(
                    tenant_id=tenant_id,
                    principal_id=initial_principal_id,
                    name=initial_principal_name,
                    platform_mappings=platform_mappings,
                    access_token=f"managed-mode-no-token:{secrets.token_urlsafe(8)}",
                )
            )

        try:
            session.commit()
        except ManagedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        except Exception as exc:
            session.rollback()
            logger.exception("Provision failed")
            return _api_error("internal_error", f"Provision failed: {exc}", 500)

        # Pull updated_at/created_at after commit so the response is accurate.
        session.refresh(new_tenant)
        created_at = new_tenant.created_at

    mcp_url, a2a_url, admin_url_path = _surface_urls(tenant_id)
    response = ProvisionTenantResponse(
        tenant_id=tenant_id,
        name=req.name,
        external_org_id=req.external_org_id,
        external_source=req.external_source,
        managed_externally=True,
        created_at=created_at,
        mcp_url=mcp_url,
        a2a_url=a2a_url,
        admin_url_path=admin_url_path,
        adapter=AdapterStatusResponse(
            type=adapter_dict["type"],
            configured=True,
            connection_test_passed=True,
            connection_test_error=None,
        ),
        initial_principal=(
            ProvisionedPrincipalResponse(principal_id=initial_principal_id, name=initial_principal_name)
            if initial_principal_id and initial_principal_name
            else None
        ),
    )
    return jsonify(response.model_dump(mode="json")), 201


@tenant_management_api.route("/tenants/preview-adapter", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(
    json=PreviewAdapterRequest,
    resp=Response(HTTP_200=PreviewAdapterResponse, HTTP_500=ApiError),
)
def preview_adapter_endpoint():
    """Probe an adapter and return network metadata — no persistence.

    Lets the Storefront UI confirm an adapter grant + auto-fill currency
    and timezone before committing to a tenant. Bad creds return 200 with
    ``ok=false`` (renders inline) — only malformed bodies / missing API key
    surface as 4xx via the normal middleware path.
    """
    req: PreviewAdapterRequest = request.context.json  # type: ignore[attr-defined]
    adapter_dict = _adapter_config_to_dict(req.adapter)
    preview = preview_adapter(adapter_dict["type"], adapter_dict)
    response = PreviewAdapterResponse(
        ok=preview.ok,
        network_name=preview.network_name,
        network_code=preview.network_code,
        currency_code=preview.currency_code,
        time_zone=preview.time_zone,
        inventory_reachable=preview.inventory_reachable,
        error=preview.error,
    )
    return jsonify(response.model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>", methods=["PATCH"])
@require_tenant_management_api_key
@spec.validate(
    json=UpdateTenantRequest,
    resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError, HTTP_400=ApiError),
)
def patch_tenant(tenant_id: str):
    """Update platform-managed fields on a tenant (PATCH semantics — only listed fields are touched)."""
    req: UpdateTenantRequest = request.context.json  # type: ignore[attr-defined]

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if req.name is not None:
            tenant.name = req.name
        if req.contact_email is not None:
            tenant.billing_contact = req.contact_email
        if req.billing_plan is not None:
            tenant.billing_plan = req.billing_plan
        tenant.updated_at = datetime.now(UTC)

        try:
            session.commit()
        except ManagedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)

        adapter_present = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        invalidate_status_cache(tenant_id)
        return jsonify(_tenant_to_detail(tenant, adapter_present))


@tenant_management_api.route("/tenants/<tenant_id>/deactivate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError))
def deactivate_tenant(tenant_id: str):
    """Idempotently deactivate a tenant. Calling on an already-inactive tenant is a no-op."""
    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if tenant.is_active:
            tenant.is_active = False
            tenant.updated_at = datetime.now(UTC)
            try:
                session.commit()
            except ManagedTenantWriteError as exc:
                session.rollback()
                return _api_error("managed_tenant_write_blocked", str(exc), 403)
            invalidate_status_cache(tenant_id)

        adapter_present = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        return jsonify(_tenant_to_detail(tenant, adapter_present))


@tenant_management_api.route("/tenants/<tenant_id>/reactivate", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantDetail, HTTP_404=ApiError))
def reactivate_tenant(tenant_id: str):
    """Idempotently reactivate a tenant."""
    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if not tenant.is_active:
            tenant.is_active = True
            tenant.updated_at = datetime.now(UTC)
            try:
                session.commit()
            except ManagedTenantWriteError as exc:
                session.rollback()
                return _api_error("managed_tenant_write_blocked", str(exc), 403)
            invalidate_status_cache(tenant_id)

        adapter_present = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first() is not None
        return jsonify(_tenant_to_detail(tenant, adapter_present))


@tenant_management_api.route("/tenants/<tenant_id>/adapter-config", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=AdapterConfigResponse, HTTP_404=ApiError))
def get_adapter_config(tenant_id: str):
    """Return the tenant's adapter config with secrets redacted."""
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
        return jsonify(_build_adapter_config_response(adapter).model_dump(mode="json"))


def _adapter_request_schema():
    """Adapter-config PUT body uses the same discriminated union as provision."""
    # Wrapper class so spectree can attach the discriminator on the JSON root.
    from pydantic import RootModel

    class AdapterConfigEnvelope(RootModel[AdapterConfigSchema]):
        model_config = {"arbitrary_types_allowed": True}

    return AdapterConfigEnvelope


_ADAPTER_PUT_SCHEMA = _adapter_request_schema()


@tenant_management_api.route("/tenants/<tenant_id>/adapter-config", methods=["PUT"])
@require_tenant_management_api_key
@spec.validate(
    json=_ADAPTER_PUT_SCHEMA,
    resp=Response(HTTP_200=AdapterConfigResponse, HTTP_400=ApiError, HTTP_404=ApiError),
)
def put_adapter_config(tenant_id: str):
    """Replace the tenant's adapter config. Tests the connection before commit."""
    body = request.context.json  # type: ignore[attr-defined]
    adapter_schema: AdapterConfigSchema = body.root
    adapter_dict = _adapter_config_to_dict(adapter_schema)

    success, error = test_adapter_connection(adapter_dict["type"], adapter_dict)
    if not success:
        return _api_error(
            "adapter_connection_failed",
            f"Adapter {adapter_dict['type']!r} connection probe failed: {error}",
            400,
            details={"adapter_type": adapter_dict["type"], "error": error},
        )

    with get_db_session() as session:
        session.info["management_api_caller"] = True

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        new_adapter = _persist_adapter_config(session, tenant_id, adapter_schema)
        try:
            session.commit()
        except ManagedTenantWriteError as exc:
            session.rollback()
            return _api_error("managed_tenant_write_blocked", str(exc), 403)
        session.refresh(new_adapter)
        invalidate_status_cache(tenant_id)
        return jsonify(_build_adapter_config_response(new_adapter).model_dump(mode="json"))


@tenant_management_api.route("/tenants/<tenant_id>/adapter-config/test-connection", methods=["POST"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TestConnectionResponse, HTTP_404=ApiError))
def adapter_test_connection(tenant_id: str):
    """Probe the saved adapter config without modifying state."""
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
        if adapter is None:
            return jsonify(
                TestConnectionResponse(
                    success=False, error="No adapter configured", tested_at=datetime.now(UTC)
                ).model_dump(mode="json")
            )

        config: dict = {}
        if adapter.adapter_type == "google_ad_manager":
            config = {
                "network_code": adapter.gam_network_code,
                "service_account_json": adapter.gam_service_account_json,
                "refresh_token": adapter.gam_refresh_token,
            }
        elif adapter.adapter_type == "mock":
            config = {"dry_run": bool(adapter.mock_dry_run)}

        success, error = test_adapter_connection(adapter.adapter_type, config)
        invalidate_status_cache(tenant_id)
        return jsonify(
            TestConnectionResponse(success=success, error=error, tested_at=datetime.now(UTC)).model_dump(mode="json")
        )


@tenant_management_api.route("/tenants/<tenant_id>/status", methods=["GET"])
@require_tenant_management_api_key
@spec.validate(resp=Response(HTTP_200=TenantStatusResponse, HTTP_404=ApiError))
def tenant_status(tenant_id: str):
    """Consolidated operational snapshot for a tenant.

    One round-trip, one cache lifetime — covers adapter health, sync runs,
    open workflows, media-buy/package counters, and creative state. The
    response is computed (not stored) and cached in-memory for ~5s.
    """
    snapshot = get_tenant_status(tenant_id)
    if snapshot is None:
        return _api_error("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)
    return jsonify(snapshot.model_dump(mode="json"))


# Register all spectree-validated routes with the OpenAPI generator.
# This is a no-op for non-validated handlers; only routes with @spec.validate participate.
spec.register(tenant_management_api)
