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

from flask import Blueprint, g, jsonify, render_template, request
from pydantic import ValidationError

from src.admin.utils import require_auth
from src.core.database.database_session import get_db_session
from src.core.database.repositories.sync_job import SyncJobAdminRepository, SyncJobRepository
from src.services.adapter_sync_orchestration import (
    SUPPORTED_SYNC_KINDS,
    AdapterDoesNotSupportSyncKind,
    enqueue_adapter_sync,
)
from src.services.sync_scheduling_view import build_scheduling_matrix

logger = logging.getLogger(__name__)

scheduling_bp = Blueprint("scheduling", __name__)

_VALID_KINDS = set(SUPPORTED_SYNC_KINDS)


@scheduling_bp.route("/scheduling", methods=["GET"])
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
                "duration_seconds": _duration_seconds(j),
                "triggered_by": j.triggered_by,
                "progress": j.progress,
                "error_message": j.error_message,
            }
            for j in recent
        ]

    return render_template(
        "scheduling.html",
        rows=[r.to_dict() for r in rows],
        recent=recent_payload,
    )


@scheduling_bp.route("/api/scheduling/jobs", methods=["GET"])
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


@scheduling_bp.route("/api/scheduling/recent", methods=["GET"])
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
                    "duration_seconds": _duration_seconds(j),
                    "triggered_by": j.triggered_by,
                    "progress": j.progress,
                    "error_message": j.error_message,
                }
                for j in jobs
            ]
        }
    )


@scheduling_bp.route("/api/scheduling/run", methods=["POST"])
@scheduling_bp.route("/admin/api/scheduling/run", methods=["POST"])
@require_auth(admin_only=True)
def run_now():
    """Enqueue one sync via the shared orchestrator.

    Body: ``{"tenant_id": "...", "adapter_type": "...", "sync_kind": "inventory"|"reporting"}``

    Returns 202 with the new ``sync_id`` so the UI can poll
    ``/admin/api/scheduling/jobs``. The actual adapter work happens on a
    daemon thread — important for GAM inventory sync which can run for
    minutes (longer than nginx's request idle timeout).

      - 202: enqueued — ``{"sync_id": "...", "status": "queued"}``
      - 400: bad request (unknown adapter / sync_kind, capability off, tenant unconfigured)
    """
    body = request.get_json(silent=True) or {}
    tenant_id = body.get("tenant_id")
    adapter_type = body.get("adapter_type")
    sync_kind = body.get("sync_kind")

    if not tenant_id or not adapter_type or not sync_kind:
        return jsonify({"error": "tenant_id, adapter_type, sync_kind are required"}), 400
    if sync_kind not in _VALID_KINDS:
        return jsonify({"error": f"sync_kind must be one of {sorted(_VALID_KINDS)}"}), 400

    triggered_by_id = _resolve_admin_identity()
    try:
        sync_id = enqueue_adapter_sync(
            tenant_id=tenant_id,
            adapter_type=adapter_type,
            sync_kind=sync_kind,
            triggered_by="admin_scheduling_ui",
            triggered_by_id=triggered_by_id,
        )
    except AdapterDoesNotSupportSyncKind as exc:
        return jsonify({"error": str(exc)}), 400
    except ValidationError as exc:
        return jsonify({"error": f"Stored adapter config is invalid: {exc}"}), 400
    except ValueError as exc:
        if "Sync already running" in str(exc):
            return (
                jsonify(
                    {
                        "error": "sync_already_running",
                        "message": str(exc),
                    }
                ),
                409,
            )
        raise
    except Exception:
        logger.exception(
            "Scheduling Run Now enqueue failed for tenant=%s adapter=%s kind=%s",
            tenant_id,
            adapter_type,
            sync_kind,
        )
        return jsonify({"error": "Enqueue failed (see server logs)"}), 500

    if sync_id is None:
        return (
            jsonify({"error": f"Tenant {tenant_id!r} is not configured for adapter {adapter_type!r}"}),
            400,
        )

    return jsonify(
        {"sync_id": sync_id, "status": _sync_status(tenant_id, sync_id), "triggered_by_id": triggered_by_id}
    ), 202


def _sync_status(tenant_id: str, sync_id: str) -> str:
    with get_db_session() as session:
        job = SyncJobRepository(session, tenant_id).find_by_sync_id(sync_id)
        return job.status if job is not None else "queued"


def _duration_seconds(job) -> int | None:
    if not job.started_at or not job.completed_at:
        return None
    return int((job.completed_at - job.started_at).total_seconds())


def _resolve_admin_identity() -> str | None:
    """Pull the super-admin's email off ``g.user`` for SyncJob attribution.

    ``g.user`` is set by :func:`require_auth` and may be a plain email
    string (legacy session shape) or a dict with ``email`` set (OAuth /
    embedded-mode shape). Returns ``None`` if neither is present so the
    SyncJob row simply lacks attribution rather than blowing up Run Now.
    """
    user = getattr(g, "user", None)
    if isinstance(user, dict):
        email = user.get("email")
        return email if isinstance(email, str) else None
    if isinstance(user, str):
        return user
    return None
