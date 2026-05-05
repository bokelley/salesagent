"""Buyer Routing page — Sprint 5 workstream B.

Renders ``/tenant/<tenant_id>/buyer-routing`` with three sections:
default GAM advertiser, routing rules, and recent activity. Read-only
in this round; editor wiring lands in workstream C, promote-to-rule
in workstream E.

See ``docs/design/embedded-mode-sprint-5-buyer-routing-ux.md``.
"""

from __future__ import annotations

import logging

from flask import Blueprint, abort, render_template
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import AdvertiserRoutingRule, Tenant
from src.services.recent_buyers_service import compute_recent_buyers

logger = logging.getLogger(__name__)

buyer_routing_bp = Blueprint("buyer_routing", __name__, url_prefix="/tenant")


# Bootstrap badge classes per ``Account.resolved_via`` enum value. The
# design doc pins these colors so publishers reading the activity table
# learn the routing chain at a glance — amber rows are fall-throughs to
# the tenant default, the publisher's "should I promote this to a rule?"
# decision point.
RESOLVED_VIA_BADGE: dict[str, str] = {
    "exact": "bg-success",  # green
    "house": "bg-primary",  # blue
    "operator": "bg-info",  # teal
    "default": "bg-warning",  # amber — the fall-through cohort
    "account": "bg-purple",  # purple — pre-mapped via /accounts
    "unknown": "bg-secondary",  # grey — legacy NULL rows
    "sandbox": "bg-dark",  # slate — sandbox carve-out
}


def _badge_class_for(resolved_via: str) -> str:
    return RESOLVED_VIA_BADGE.get(resolved_via, "bg-secondary")


@buyer_routing_bp.route("/<tenant_id>/buyer-routing", strict_slashes=False)
@require_tenant_access()
def buyer_routing_page(tenant_id: str):
    """Render the buyer-routing page (read-only this round).

    Editor wiring (default-advertiser save, routing-rule CRUD,
    promote-to-rule) is deferred to workstreams C/E. This handler
    only reads existing data so the publisher can see the page shape.
    """
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            abort(404, description=f"Tenant {tenant_id!r} not found")

        # FIXME(embedded-mode-sprint-5-piece-B): fold into BuyerRoutingRepository
        # once the editor (workstream C) lands its CRUD repository.
        rules = session.scalars(
            select(AdvertiserRoutingRule)
            .filter_by(tenant_id=tenant_id)
            # Most-specific first matches the resolution chain ordering
            # in src/services/buyer_advertiser_routing.py — exact wins
            # over house wildcard wins over operator wildcard.
            .order_by(
                AdvertiserRoutingRule.brand_id.is_(None).asc(),
                AdvertiserRoutingRule.brand_house.is_(None).asc(),
                AdvertiserRoutingRule.operator_domain.asc(),
            )
        ).all()

        # Snapshot what the template needs so we can close the session
        # before render_template runs (Jinja attribute access on a
        # detached ORM model is fine, but the routing-rule list is
        # small — copying is simpler than worrying about lazy loads).
        rule_rows = [
            {
                # principal_id may not exist as a column yet — workstream A0
                # adds it in parallel. ``getattr`` lets us ship before/after
                # without a hard dependency. SQLAlchemy attribute access on
                # a not-yet-migrated column raises only at query time, not
                # attribute-read time on the loaded ORM instance.
                "principal_id": getattr(rule, "principal_id", None),
                "operator_domain": rule.operator_domain,
                "brand_house": rule.brand_house,
                "brand_id": rule.brand_id,
                "gam_advertiser_id": rule.gam_advertiser_id,
                "id": rule.id,
            }
            for rule in rules
        ]

        default_gam_advertiser_id = tenant.default_gam_advertiser_id
        is_embedded = bool(tenant.is_embedded)
        tenant_name = tenant.name

    recent_rows = compute_recent_buyers(tenant_id, days=30, limit=100)

    activity_rows = []
    sandbox_rows = []
    for row in recent_rows:
        view_row = {
            "principal_id": None,  # workstream A0 will plumb agent through Account
            "operator_domain": row.operator_domain,
            "brand_house": row.brand_house,
            "brand_id": row.brand_id,
            "last_seen_at": row.last_seen_at,
            "request_count": row.request_count,
            "resolved_gam_advertiser_id": row.resolved_gam_advertiser_id,
            "resolved_via": row.resolved_via,
            "badge_class": _badge_class_for(row.resolved_via),
        }
        if row.sandbox:
            sandbox_rows.append(view_row)
        else:
            activity_rows.append(view_row)

    return render_template(
        "buyer_routing.html",
        tenant={
            "tenant_id": tenant_id,
            "name": tenant_name,
            "is_embedded": is_embedded,
            "default_gam_advertiser_id": default_gam_advertiser_id,
        },
        tenant_id=tenant_id,
        rules=rule_rows,
        activity=activity_rows,
        sandbox_rows=sandbox_rows,
    )
