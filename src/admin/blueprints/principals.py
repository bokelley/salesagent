"""Principals (Advertisers) management blueprint for admin UI."""

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from src.admin.services import DashboardService
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal, PushNotificationConfig, Tenant

logger = logging.getLogger(__name__)

# Create Blueprint (url_prefix is set during registration in app.py)
principals_bp = Blueprint("principals", __name__)


@principals_bp.route("/principals")
@require_tenant_access()
def list_principals(tenant_id):
    """List all principals (advertisers) for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            stmt = select(Principal).filter_by(tenant_id=tenant_id).order_by(Principal.name)
            principals = db_session.scalars(stmt).all()

            # Convert to dict format for template
            principals_list = []
            for principal in principals:
                # Count media buys for this principal
                stmt = (
                    select(func.count())
                    .select_from(MediaBuy)
                    .filter_by(tenant_id=tenant_id, principal_id=principal.principal_id)
                )
                media_buy_count = db_session.scalar(stmt)

                # Handle both string (SQLite) and dict (PostgreSQL JSONB) formats
                mappings = principal.platform_mappings
                if mappings and isinstance(mappings, str):
                    mappings = json.loads(mappings)
                elif not mappings:
                    mappings = {}

                principal_dict = {
                    "principal_id": principal.principal_id,
                    "name": principal.name,
                    "access_token": principal.access_token,
                    "platform_mappings": mappings,
                    "media_buy_count": media_buy_count,
                    "created_at": principal.created_at,
                }
                principals_list.append(principal_dict)

            # Get dashboard metrics that the template expects
            dashboard_service = DashboardService(tenant_id)
            metrics = dashboard_service.get_dashboard_metrics()

            # Get recent media buys that the template expects
            recent_media_buys = dashboard_service.get_recent_media_buys(limit=10)

            # Get chart data that the template expects
            chart_data_dict = dashboard_service.get_chart_data()

            # Get tenant config for features
            from src.admin.utils import get_tenant_config_from_db

            config = get_tenant_config_from_db(tenant_id)
            features = config.get("features", {})

            # The template expects this to be under the 'advertisers' key
            # since principals are advertisers in the UI
            return render_template(
                "tenant_dashboard.html",
                tenant=tenant,
                tenant_id=tenant_id,
                advertisers=principals_list,
                # Template variables to match main dashboard
                active_campaigns=metrics.get("live_buys", 0),
                total_spend=metrics.get("total_revenue", 0),
                principals_count=metrics.get("total_advertisers", 0),
                products_count=metrics.get("products_count", 0),
                recent_buys=recent_media_buys,
                recent_media_buys=recent_media_buys,
                features=features,
                # Chart data
                revenue_data=json.dumps(metrics["revenue_data"]),
                chart_labels=chart_data_dict["labels"],
                chart_data=chart_data_dict["data"],
                # Metrics object
                metrics=metrics,
                show_advertisers_tab=True,
            )

    except Exception as e:
        logger.error(f"Error listing principals: {e}", exc_info=True)
        flash("Error loading advertisers", "error")
        return redirect(url_for("core.index"))


# Resolve-domain rate limit: per (tenant_id, user_email) sliding window.
# Each call triggers up to ~5 outbound HTTPS fetches (brand.json + JWKS), so
# we cap admit-time discovery at 10 calls per minute per operator. In-memory
# is fine for single-worker dev; multi-worker prod should swap for Redis.
_RESOLVE_DOMAIN_RATE: dict[tuple[str, str], list[float]] = {}
_RESOLVE_DOMAIN_WINDOW_SECONDS = 60.0
_RESOLVE_DOMAIN_LIMIT = 10


def _check_resolve_domain_rate(tenant_id: str, user_key: str) -> bool:
    """Return True if the caller may proceed; False if rate-limited."""
    import time as _time

    now = _time.monotonic()
    key = (tenant_id, user_key)
    entries = _RESOLVE_DOMAIN_RATE.setdefault(key, [])
    # Drop entries outside the window
    cutoff = now - _RESOLVE_DOMAIN_WINDOW_SECONDS
    while entries and entries[0] < cutoff:
        entries.pop(0)
    if len(entries) >= _RESOLVE_DOMAIN_LIMIT:
        return False
    entries.append(now)
    return True


@principals_bp.route("/principals/resolve-domain", methods=["POST"])
@require_tenant_access()
def resolve_domain(tenant_id):
    """Look up a buyer's published agent metadata from their domain.

    Body: ``{"domain": "interchange.io"}``. Returns a preview the create
    form can render before the operator confirms admission. Never raises —
    a failed lookup returns ``{"ok": false, "error": "..."}``.
    """
    from flask import session as flask_session

    from src.admin.services.buyer_agent_resolve import resolve_domain as _resolve

    raw_user = flask_session.get("user")
    if isinstance(raw_user, dict):
        user_key = raw_user.get("email") or "unknown"
    elif isinstance(raw_user, str):
        user_key = raw_user
    else:
        user_key = flask_session.get("user_email") or "unknown"
    if not _check_resolve_domain_rate(tenant_id, user_key):
        return jsonify({"ok": False, "error": "rate-limited; try again in a minute"}), 429

    payload = request.get_json(silent=True) or {}
    raw = (payload.get("domain") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "missing 'domain'"}), 400

    result = _resolve(raw)
    # The create handler enforces uniqueness on submit (existing
    # duplicate-name check + unique index). The preview is read-only —
    # showing "already added" here would be a UX nicety only, and adding
    # a Principal repo just for that lookup is over-engineering when the
    # submit-side check already covers the failure mode.
    return jsonify(result.to_dict())


@principals_bp.route("/principals/create", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action(
    "create_principal",
    extract_details=lambda r, **kw: {"name": request.form.get("name")} if request.method == "POST" else {},
)
def create_principal(tenant_id):
    """Create a new principal (advertiser) for a tenant."""
    if request.method == "GET":
        # Get tenant info for GAM configuration
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Check if GAM is configured (uses centralized tenant.is_gam_tenant property)
            has_gam = tenant.is_gam_tenant

            return render_template(
                "create_principal.html",
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                has_gam=has_gam,
            )

    # POST - Create the principal
    try:
        principal_name = request.form.get("name", "").strip()
        if not principal_name:
            flash("Principal name is required", "error")
            return redirect(request.url)

        # Generate unique ID and token
        principal_id = f"prin_{uuid.uuid4().hex[:8]}"
        access_token = f"tok_{secrets.token_urlsafe(32)}"

        # Build platform mappings
        platform_mappings = {}

        # GAM advertiser mapping
        gam_advertiser_id = request.form.get("gam_advertiser_id", "").strip()
        if gam_advertiser_id:
            # Validate it's numeric (GAM expects integer company IDs)
            try:
                int(gam_advertiser_id)
            except (ValueError, TypeError):
                flash(
                    f"GAM Advertiser ID must be numeric (got: '{gam_advertiser_id}'). "
                    "Please select a valid advertiser from the dropdown.",
                    "error",
                )
                return redirect(request.url)

            platform_mappings["google_ad_manager"] = {
                "advertiser_id": gam_advertiser_id,
                "enabled": True,
            }

        # Mock adapter mapping (for testing)
        if request.form.get("enable_mock"):
            platform_mappings["mock"] = {
                "advertiser_id": f"mock_{principal_id}",
                "enabled": True,
            }

        with get_db_session() as db_session:
            # Check if principal name already exists
            existing = db_session.scalars(select(Principal).filter_by(tenant_id=tenant_id, name=principal_name)).first()
            if existing:
                flash(f"An advertiser named '{principal_name}' already exists", "error")
                return redirect(request.url)

            # Optional signing config. brand_domain is the trust anchor
            # (operator-typed buyer domain); the verifier walks
            # https://<brand_domain>/.well-known/brand.json on every signed
            # request via BrandJsonJwksResolver. agent_url is informational
            # (audit-log stamping); rotation in brand.json propagates without
            # operator action.
            brand_domain = (request.form.get("brand_domain", "") or "").strip() or None
            agent_url = (request.form.get("agent_url", "") or "").strip() or None
            signing_required = bool(request.form.get("signing_required"))
            if signing_required and not brand_domain:
                flash("Cannot require signed requests without a buyer domain", "error")
                return redirect(request.url)

            # Strict-admit guard: a brand-new principal cannot start with
            # signing_required=true because no signed request from them has
            # been verified yet. Redirect to edit page after admit so the
            # operator can flip the switch once the buyer's first signed
            # request lands.
            if signing_required:
                flash(
                    "Created without 'require signed requests' — the buyer must send "
                    "a signed request before that switch can be enabled. The buyer-agent "
                    "edit page will show 🟢 once verification lands.",
                    "warning",
                )
                signing_required = False

            # Create the principal
            principal = Principal(
                tenant_id=tenant_id,
                principal_id=principal_id,
                name=principal_name,
                access_token=access_token,
                platform_mappings=platform_mappings,  # JSONType handles serialization
                agent_url=agent_url,
                brand_domain=brand_domain,
                signing_required=signing_required,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            db_session.add(principal)
            db_session.commit()

            flash(f"Advertiser '{principal_name}' created successfully", "success")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="advertisers"))

    except Exception as e:
        logger.error(f"Error creating principal: {e}", exc_info=True)
        flash("Error creating advertiser", "error")
        return redirect(request.url)


@principals_bp.route("/principals/<principal_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action(
    "edit_principal",
    extract_details=lambda r, **kw: {"principal_id": kw.get("principal_id")} if request.method == "POST" else {},
)
def edit_principal(tenant_id, principal_id):
    """Edit an existing principal - reuses create_principal.html template."""
    if request.method == "GET":
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            if not principal:
                flash("Advertiser not found", "error")
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

            # Check if GAM is configured (uses centralized tenant.is_gam_tenant property)
            has_gam = tenant.is_gam_tenant

            # Extract existing GAM advertiser ID if present
            existing_gam_id = None
            if principal.platform_mappings:
                mappings = principal.platform_mappings if isinstance(principal.platform_mappings, dict) else {}
                gam_mapping = mappings.get("google_ad_manager", {})
                existing_gam_id = gam_mapping.get("advertiser_id")

            # Verification status block. Reads ``principals.last_signed_verified_at``
            # (cached on the row by the verifier middleware) — independent of
            # audit-log retention. The audit row lookup is a fallback for
            # the kid display, since the column doesn't carry it.
            verification_status = {
                "has_verification": principal.last_signed_verified_at is not None,
                "timestamp": principal.last_signed_verified_at,
                "key_id": None,
                "agent_url": principal.agent_url,
            }
            if principal.last_signed_verified_at is not None:
                from src.core.database.repositories.audit_log import AuditLogRepository

                last_verified = AuditLogRepository(db_session, tenant_id).last_signed_verification_for_principal(
                    principal_id
                )
                if last_verified is not None:
                    verification_status["key_id"] = last_verified.verified_key_id

            return render_template(
                "create_principal.html",
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                has_gam=has_gam,
                edit_mode=True,
                principal=principal,
                existing_gam_id=existing_gam_id,
                verification_status=verification_status,
            )

    # POST - Update the principal
    try:
        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            if not principal:
                flash("Advertiser not found", "error")
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

            # Validate inputs and run admit-time guards BEFORE mutating the
            # principal — a downstream validation failure must not partially
            # save a strict-admit flip.

            # Signing config. brand_domain is the trust anchor; agent_url is
            # informational. The verifier walks brand.json on every request,
            # so agent_url rotation in brand.json doesn't need operator
            # action — only brand_domain change resets evidence.
            brand_domain = (request.form.get("brand_domain", "") or "").strip() or None
            agent_url = (request.form.get("agent_url", "") or "").strip() or None
            signing_required = bool(request.form.get("signing_required"))
            if signing_required and not brand_domain:
                flash("Cannot require signed requests without a buyer domain", "error")
                return redirect(request.url)

            # Reset the verification snapshot when brand_domain changes —
            # past verifications were against a different trust root and
            # must not satisfy the strict-admit guard for the new config.
            # agent_url change does NOT reset; the verifier walks brand.json
            # so agent_url rotation is handled automatically by the library.
            brand_changed = brand_domain != principal.brand_domain
            if brand_changed:
                principal.last_signed_verified_at = None
                # Drop the cached BrandJsonJwksResolver so the next verify
                # walks the new trust root rather than the stale snapshot.
                from src.core.signing import get_buyer_agent_jwks_cache

                get_buyer_agent_jwks_cache().invalidate(tenant_id, principal_id)

            # Strict-admit guard: refuse to flip signing_required=true unless
            # a signed request from THIS principal under the CURRENT
            # brand_domain has been verified. The cached column is reset
            # above on brand_domain change, so stale evidence can't satisfy
            # the guard for a new trust root.
            if signing_required and not principal.signing_required:
                if principal.last_signed_verified_at is None:
                    flash(
                        "Refusing to require signed requests: no signed request from "
                        "this buyer has been verified under the current brand domain "
                        "yet. Ask the buyer to send a signed request first; this page "
                        "will show 🟢 once it lands.",
                        "error",
                    )
                    return redirect(request.url)

            # GAM advertiser mapping (validated before assignment)
            gam_advertiser_id = request.form.get("gam_advertiser_id", "").strip()
            if gam_advertiser_id:
                try:
                    int(gam_advertiser_id)
                except (ValueError, TypeError):
                    flash("GAM Advertiser ID must be numeric", "error")
                    return redirect(request.url)

            # Update name if provided
            principal_name = request.form.get("name", "").strip()
            if principal_name:
                principal.name = principal_name

            # Preserve non-GAM mappings (e.g. mock adapter for tests) so edit
            # doesn't accidentally drop them. Replace google_ad_manager only
            # when a new advertiser_id is submitted.
            existing_mappings = principal.platform_mappings if isinstance(principal.platform_mappings, dict) else {}
            new_mappings = dict(existing_mappings)
            if gam_advertiser_id:
                new_mappings["google_ad_manager"] = {
                    "advertiser_id": gam_advertiser_id,
                    "enabled": True,
                }
            principal.platform_mappings = new_mappings

            principal.agent_url = agent_url
            principal.brand_domain = brand_domain
            principal.signing_required = signing_required

            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            flash(f"Advertiser '{principal.name}' updated successfully", "success")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="advertisers"))

    except Exception as e:
        logger.error(f"Error updating principal: {e}", exc_info=True)
        flash("Error updating advertiser", "error")
        return redirect(request.url)


@principals_bp.route("/principal/<principal_id>", methods=["GET"])
@require_tenant_access()
def get_principal(tenant_id, principal_id):
    """Get principal details including platform mappings (API endpoint)."""
    try:
        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse platform mappings (handle both string and dict formats)
            if principal.platform_mappings:
                if isinstance(principal.platform_mappings, str):
                    mappings = json.loads(principal.platform_mappings)
                else:
                    mappings = principal.platform_mappings
            else:
                mappings = {}

            return jsonify(
                {
                    "success": True,
                    "principal": {
                        "principal_id": principal.principal_id,
                        "name": principal.name,
                        "access_token": principal.access_token,
                        "platform_mappings": mappings,
                        "created_at": principal.created_at.isoformat() if principal.created_at else None,
                    },
                }
            )

    except Exception as e:
        logger.error(f"Error getting principal {principal_id}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to get principal: {str(e)}"}), 500


@principals_bp.route("/principal/<principal_id>/update_mappings", methods=["POST"])
@log_admin_action("update_mappings")
@require_tenant_access()
def update_mappings(tenant_id, principal_id):
    """Update principal platform mappings."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request"}), 400

        platform_mappings = data.get("platform_mappings", {})

        # Validate GAM advertiser_id if present
        if "google_ad_manager" in platform_mappings:
            gam_config = platform_mappings["google_ad_manager"]
            advertiser_id = gam_config.get("advertiser_id") or gam_config.get("company_id")

            if advertiser_id:
                # Validate it's numeric (GAM expects integer company IDs)
                try:
                    int(advertiser_id)
                except (ValueError, TypeError):
                    return (
                        jsonify(
                            {
                                "error": f"GAM Advertiser ID must be numeric (got: '{advertiser_id}'). "
                                "Please select a valid advertiser from the dropdown."
                            }
                        ),
                        400,
                    )

        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Update mappings - JSONType handles serialization
            principal.platform_mappings = platform_mappings
            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            return jsonify(
                {
                    "success": True,
                    "message": "Platform mappings updated successfully",
                }
            )

    except Exception as e:
        logger.error(f"Error updating principal mappings: {e}", exc_info=True)
        return jsonify({"error": "Failed to update mappings"}), 500


@principals_bp.route("/api/gam/get-advertisers", methods=["POST"])
@log_admin_action("get_gam_advertisers")
@require_tenant_access()
def get_gam_advertisers(tenant_id):
    """Get list of advertisers from GAM for a tenant.

    Request body (JSON):
        search: Optional search query to filter by name (uses LIKE '%query%')
        limit: Maximum results to return (default: 500, max: 500)
        fetch_all: If true, fetches ALL advertisers with pagination (can be slow)

    Performance Notes:
        - For networks with 1000+ advertisers, use 'search' to filter results
        - fetch_all=true can take 5-10 seconds for networks with thousands of advertisers
        - Default behavior (limit=500) is fast but may not return all advertisers
    """
    try:
        from src.adapters.google_ad_manager import GoogleAdManager

        # Get request parameters
        data = request.get_json() or {}
        search_query = data.get("search")
        limit = data.get("limit", 500)
        fetch_all = data.get("fetch_all", False)

        # Get tenant configuration
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Check if GAM is configured (uses centralized tenant.is_gam_tenant property)
            gam_enabled = tenant.is_gam_tenant

            # Debug logging to help troubleshoot
            logger.info(
                f"GAM API detection for tenant {tenant_id}: "
                f"ad_server={tenant.ad_server}, "
                f"has_adapter_config={tenant.adapter_config is not None}, "
                f"adapter_type={tenant.adapter_config.adapter_type if tenant.adapter_config else None}, "
                f"gam_enabled={gam_enabled}, "
                f"search={search_query}, limit={limit}, fetch_all={fetch_all}"
            )

            if not gam_enabled:
                logger.warning(f"GAM not enabled for tenant {tenant_id}")
                return jsonify({"error": "Google Ad Manager not configured"}), 400

            # Initialize GAM adapter with adapter config
            try:
                # Import Principal model
                from src.core.schemas import Principal

                # Create a mock principal for GAM initialization
                # Need dummy advertiser_id for GAM adapter validation, even though get_advertisers() doesn't use it
                mock_principal = Principal(
                    principal_id="system",
                    name="System",
                    platform_mappings={
                        "google_ad_manager": {
                            "advertiser_id": "system_temp_advertiser_id",  # Dummy ID for validation only
                            "advertiser_name": "System (temp)",
                        }
                    },
                )

                # Build GAM config from AdapterConfig
                if not tenant.adapter_config or not tenant.adapter_config.gam_network_code:
                    return jsonify({"error": "GAM network code not configured for this tenant"}), 400

                # Use build_gam_config_from_adapter to handle both OAuth and service account
                from src.adapters.gam import build_gam_config_from_adapter

                gam_config = build_gam_config_from_adapter(tenant.adapter_config)

                adapter = GoogleAdManager(
                    config=gam_config,
                    principal=mock_principal,
                    network_code=tenant.adapter_config.gam_network_code,
                    advertiser_id=None,
                    trafficker_id=tenant.adapter_config.gam_trafficker_id,
                    dry_run=False,
                    tenant_id=tenant_id,
                )

                # Get advertisers (companies) from GAM with filtering support
                advertisers = adapter.orders_manager.get_advertisers(
                    search_query=search_query, limit=limit, fetch_all=fetch_all
                )

                return jsonify(
                    {
                        "success": True,
                        "advertisers": advertisers,
                        "count": len(advertisers),
                        "search": search_query,
                        "fetch_all": fetch_all,
                    }
                )

            except Exception as gam_error:
                logger.error(f"GAM API error: {gam_error}")
                return jsonify({"error": f"Failed to fetch advertisers: {str(gam_error)}"}), 500

    except Exception as e:
        logger.error(f"Error getting GAM advertisers: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@principals_bp.route("/api/principal/<principal_id>/config", methods=["GET"])
@require_tenant_access()
def get_principal_config(tenant_id, principal_id):
    """Get principal configuration including platform mappings for testing UI."""
    try:
        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse platform mappings
            platform_mappings = (
                json.loads(principal.platform_mappings)
                if isinstance(principal.platform_mappings, str)
                else principal.platform_mappings
            )

            return jsonify(
                {
                    "principal_id": principal.principal_id,
                    "name": principal.name,
                    "platform_mappings": platform_mappings,
                }
            )

    except Exception as e:
        logger.error(f"Error getting principal config: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@principals_bp.route("/api/principal/<principal_id>/testing-config", methods=["POST"])
@log_admin_action("save_testing_config")
@require_tenant_access()
def save_testing_config(tenant_id, principal_id):
    """Save testing configuration (HITL settings) for a mock adapter principal."""
    try:
        data = request.get_json()
        if not data or "hitl_config" not in data:
            return jsonify({"error": "Missing hitl_config in request"}), 400

        hitl_config = data["hitl_config"]

        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse existing platform mappings
            platform_mappings = (
                json.loads(principal.platform_mappings)
                if isinstance(principal.platform_mappings, str)
                else principal.platform_mappings or {}
            )

            # Ensure mock adapter exists
            if "mock" not in platform_mappings:
                platform_mappings["mock"] = {"advertiser_id": f"mock_{principal_id}", "enabled": True}

            # Update hitl_config
            platform_mappings["mock"]["hitl_config"] = hitl_config

            # Save back to database - JSONType handles serialization
            principal.platform_mappings = platform_mappings
            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            logger.info(f"Updated testing config for principal {principal_id} in tenant {tenant_id}")

            return jsonify({"success": True, "message": "Testing configuration saved successfully"})

    except Exception as e:
        logger.error(f"Error saving testing config: {e}", exc_info=True)
        return jsonify({"error": "Failed to save testing configuration"}), 500


@principals_bp.route("/principals/<principal_id>/webhooks", methods=["GET"])
@require_tenant_access()
def manage_webhooks(tenant_id, principal_id):
    """Manage webhook configurations for a principal."""
    try:
        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            if not principal:
                flash("Principal not found", "error")
                return redirect(url_for("principals.list_principals", tenant_id=tenant_id))

            # Get all webhooks for this principal
            webhooks = db_session.scalars(
                select(PushNotificationConfig).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).all()

            return render_template(
                "webhook_management.html",
                tenant_id=tenant_id,
                principal=principal,
                webhooks=webhooks,
            )

    except Exception as e:
        logger.error(f"Error loading webhook management: {e}", exc_info=True)
        flash(f"Error loading webhooks: {str(e)}", "error")
        return redirect(url_for("principals.list_principals", tenant_id=tenant_id))


@principals_bp.route("/principals/<principal_id>/webhooks/register", methods=["POST"])
@log_admin_action("register_webhook")
@require_tenant_access()
def register_webhook(tenant_id, principal_id):
    """Register a new webhook for a principal."""
    try:
        from src.core.webhook_validator import WebhookURLValidator

        url = request.form.get("url")
        auth_type = request.form.get("auth_type", "none")

        # Validate URL for SSRF protection
        is_valid, error_msg = WebhookURLValidator.validate_webhook_url(url)
        if not is_valid:
            flash(f"Invalid webhook URL: {error_msg}", "error")
            return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))

        # Build auth config based on type
        auth_config = {}
        if auth_type == "hmac_sha256":
            secret = request.form.get("hmac_secret")
            if not secret:
                flash("HMAC secret is required for HMAC authentication", "error")
                return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))
            auth_config = {"secret": secret}

        with get_db_session() as db_session:
            # Check if webhook already exists
            stmt = select(PushNotificationConfig).filter_by(tenant_id=tenant_id, principal_id=principal_id, url=url)
            existing = db_session.scalars(stmt).first()

            if existing:
                flash("Webhook URL already registered for this principal", "warning")
                return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))

            # Create new webhook
            webhook = PushNotificationConfig(
                config_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                url=url,
                auth_type=auth_type if auth_type != "none" else None,
                auth_config=auth_config if auth_config else None,
                is_active=True,
                created_at=datetime.now(UTC),
            )

            db_session.add(webhook)
            db_session.commit()

            logger.info(f"Registered webhook {url} for principal {principal_id} in tenant {tenant_id}")
            flash("Webhook registered successfully", "success")

        return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))

    except Exception as e:
        logger.error(f"Error registering webhook: {e}", exc_info=True)
        flash(f"Error registering webhook: {str(e)}", "error")
        return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))


@principals_bp.route("/principals/<principal_id>/webhooks/<config_id>/delete", methods=["POST"])
@log_admin_action("delete_webhook")
@require_tenant_access()
def delete_webhook(tenant_id, principal_id, config_id):
    """Delete a webhook configuration."""
    try:
        with get_db_session() as db_session:
            stmt = select(PushNotificationConfig).filter_by(
                tenant_id=tenant_id, principal_id=principal_id, config_id=config_id
            )
            webhook = db_session.scalars(stmt).first()

            if not webhook:
                flash("Webhook not found", "error")
                return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))

            db_session.delete(webhook)
            db_session.commit()

            logger.info(f"Deleted webhook {config_id} for principal {principal_id} in tenant {tenant_id}")
            flash("Webhook deleted successfully", "success")

        return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))

    except Exception as e:
        logger.error(f"Error deleting webhook: {e}", exc_info=True)
        flash(f"Error deleting webhook: {str(e)}", "error")
        return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))


@principals_bp.route("/principals/<principal_id>/webhooks/<config_id>/toggle", methods=["POST"])
@log_admin_action("toggle_webhook")
@require_tenant_access()
def toggle_webhook(tenant_id, principal_id, config_id):
    """Toggle webhook active status."""
    try:
        with get_db_session() as db_session:
            stmt = select(PushNotificationConfig).filter_by(
                tenant_id=tenant_id, principal_id=principal_id, config_id=config_id
            )
            webhook = db_session.scalars(stmt).first()

            if not webhook:
                return jsonify({"error": "Webhook not found"}), 404

            webhook.is_active = not webhook.is_active
            db_session.commit()

            logger.info(
                f"Toggled webhook {config_id} to {'active' if webhook.is_active else 'inactive'} for principal {principal_id}"
            )

            return jsonify({"success": True, "is_active": webhook.is_active})

    except Exception as e:
        logger.error(f"Error toggling webhook: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@principals_bp.route("/principals/<principal_id>/delete", methods=["DELETE", "POST"])
@log_admin_action("delete_principal")
@require_tenant_access()
def delete_principal(tenant_id, principal_id):
    """Delete a principal (advertiser)."""
    try:
        with get_db_session() as db_session:
            # Find the principal
            stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            principal = db_session.scalars(stmt).first()

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            principal_name = principal.name

            # Delete the principal (cascades to related records)
            db_session.delete(principal)
            db_session.commit()

            logger.info(f"Deleted principal {principal_id} ({principal_name}) from tenant {tenant_id}")

            return jsonify({"success": True, "message": f"Principal '{principal_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting principal: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
