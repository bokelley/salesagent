"""Cross-tenant adapter scheduling page (#382 Stage 4).

Super-admin-only page at ``/admin/scheduling`` that shows the freshness
matrix across every configured ``(tenant, adapter, sync_kind)`` triple
the platform knows how to run. "Run Now" buttons dispatch through the
shared orchestrator from Stage 3 so the page can't drift away from the
scheduler path.

Endpoints:
  - ``GET  /admin/scheduling``                   — HTML page
  - ``GET  /admin/api/scheduling/jobs``          — JSON listing (powers SPA refreshes)
  - ``POST /admin/api/scheduling/run``           — kick off a sync for one row
  - ``GET  /admin/api/scheduling/recent``        — recent runs feed (any tenant)
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from pydantic import ValidationError

from src.admin.utils import require_auth
from src.core.database.database_session import get_db_session
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services.adapter_sync_orchestration import (
    KIND_INVENTORY,
    KIND_REPORTING,
    AdapterDoesNotSupportSyncKind,
    execute_adapter_sync,
)
from src.services.sync_scheduling_view import build_scheduling_matrix

logger = logging.getLogger(__name__)

scheduling_bp = Blueprint("scheduling", __name__)

_VALID_KINDS = {KIND_INVENTORY, KIND_REPORTING}


@scheduling_bp.route("/admin/scheduling", methods=["GET"])
@require_auth(admin_only=True)
def scheduling_index():
    """Render the cross-tenant scheduling page.

    Initial render embeds the matrix; the page's JS refreshes from
    ``/admin/api/scheduling/jobs`` after every Run Now click so the user
    doesn't have to reload to see the new status.
    """
    with get_db_session() as session:
        rows = build_scheduling_matrix(session)
        recent = SyncJobAdminRepository(session).list_recent(limit=20)
        recent_payload = [
            {
                "sync_id": j.sync_id,
                "tenant_id": j.tenant_id,
                "adapter_type": j.adapter_type,
                "sync_type": j.sync_type,
                "status": j.status,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "triggered_by": j.triggered_by,
                "error_message": j.error_message,
            }
            for j in recent
        ]

    return render_template(
        "scheduling.html",
        rows=[r.to_dict() for r in rows],
        recent=recent_payload,
    )


@scheduling_bp.route("/admin/api/scheduling/jobs", methods=["GET"])
@require_auth(admin_only=True)
def list_jobs():
    """Return the full scheduling matrix as JSON.

    Used by the page's in-page JS after a Run Now click — no params,
    cheap enough at the (tenant, adapter, kind) cardinality we expect.
    """
    with get_db_session() as session:
        rows = build_scheduling_matrix(session)
    return jsonify({"rows": [r.to_dict() for r in rows]})


@scheduling_bp.route("/admin/api/scheduling/recent", methods=["GET"])
@require_auth(admin_only=True)
def list_recent():
    """Return the N most-recent SyncJob rows across all tenants."""
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    limit = max(1, min(limit, 200))

    with get_db_session() as session:
        jobs = SyncJobAdminRepository(session).list_recent(limit=limit)
    return jsonify(
        {
            "jobs": [
                {
                    "sync_id": j.sync_id,
                    "tenant_id": j.tenant_id,
                    "adapter_type": j.adapter_type,
                    "sync_type": j.sync_type,
                    "status": j.status,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                    "triggered_by": j.triggered_by,
                    "error_message": j.error_message,
                }
                for j in jobs
            ]
        }
    )


@scheduling_bp.route("/admin/api/scheduling/run", methods=["POST"])
@require_auth(admin_only=True)
def run_now():
    """Dispatch one sync via the shared orchestrator.

    Body: ``{"tenant_id": "...", "adapter_type": "...", "sync_kind": "inventory"|"reporting"}``

    Returns the SyncExecutionResult shape with HTTP status reflecting outcome:
      - 200: succeeded
      - 400: bad request (unknown adapter / sync_kind, capability off)
      - 503: ``scope_pending=True`` (FW reporting awaiting IAM grant)
      - 500: succeeded=False but not scope-pending (generic adapter failure)
    """
    body = request.get_json(silent=True) or {}
    tenant_id = body.get("tenant_id")
    adapter_type = body.get("adapter_type")
    sync_kind = body.get("sync_kind")

    if not tenant_id or not adapter_type or not sync_kind:
        return jsonify({"error": "tenant_id, adapter_type, sync_kind are required"}), 400
    if sync_kind not in _VALID_KINDS:
        return jsonify({"error": f"sync_kind must be one of {sorted(_VALID_KINDS)}"}), 400

    try:
        # ``triggered_by_id`` could carry the admin email, but the SyncJob's
        # ``triggered_by`` column is already provenance enough for this page —
        # admins acting on this page are super-admins by gate, not impersonating
        # a tenant principal.
        result = execute_adapter_sync(
            tenant_id=tenant_id,
            adapter_type=adapter_type,
            sync_kind=sync_kind,
            triggered_by="admin_scheduling_ui",
        )
    except AdapterDoesNotSupportSyncKind as exc:
        return jsonify({"error": str(exc)}), 400
    except ValidationError as exc:
        return jsonify({"error": f"Stored adapter config is invalid: {exc}"}), 400
    except Exception:
        logger.exception(
            "Scheduling Run Now failed for tenant=%s adapter=%s kind=%s", tenant_id, adapter_type, sync_kind
        )
        return jsonify({"error": "Sync failed (see server logs)"}), 500

    if result is None:
        return (
            jsonify({"error": f"Tenant {tenant_id!r} is not configured for adapter {adapter_type!r}"}),
            400,
        )

    payload = {
        "sync_id": result.sync_id,
        "sync_kind": result.sync_kind,
        "succeeded": result.succeeded,
        "counts": result.counts,
        "errors": result.errors,
        "metadata": result.metadata,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
    }

    if result.scope_pending:
        return jsonify({**payload, "scope_pending": True}), 503
    if not result.succeeded:
        return jsonify(payload), 500
    return jsonify(payload), 200
