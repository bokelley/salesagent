"""Tests for the Workflows admin page template.

Covers tescoboy issues #142 and #159:

- #142: the Media Buys tab "Review & Approve" link must point at the detail
  page (GET), not the POST-only ``/approve`` endpoint that was returning 405.

- #159: the Tasks tab must expose an Actions column. Rows in
  ``requires_approval`` / ``pending_approval`` must surface a primary
  Review & Approve link to ``workflows.review_workflow_step``; other rows
  must surface a quiet View link to the same page. Without this column,
  non-``create_media_buy`` approvals (e.g. ``update_media_buy``) are
  unreachable through normal navigation.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.admin.app import create_app


@pytest.fixture
def rendered_workflows_html():
    """Render the workflows template with a tenant + tasks fixture in app context."""
    app = create_app()

    tenant = MagicMock(tenant_id="t1", name="Test Tenant")

    pending_buy = MagicMock(
        media_buy_id="mb_pending",
        status="pending_approval",
        created_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
        # Avoid attribute errors on optional template fields:
        principal_name="Acme",
        order_name="Order-1",
        budget=1000.0,
    )
    pending_buy.configure_mock(name="Pending Buy")

    pending_task = {
        "step_id": "step_pending_001",
        "context_id": "ctx_001",
        "step_type": "tool",
        "tool_name": "update_media_buy",
        "step_name": "update_media_buy",
        "status": "requires_approval",
        "assigned_to": "publisher",
        "principal_name": "Acme",
        "created_at": datetime(2026, 5, 7, 13, 0, tzinfo=UTC),
        "completed_at": None,
        "error_message": None,
        "request_data": {},
    }
    completed_task = {
        **pending_task,
        "step_id": "step_done_001",
        "context_id": "ctx_002",
        "status": "completed",
        "completed_at": datetime(2026, 5, 7, 13, 5, tzinfo=UTC),
    }

    with app.test_request_context("/tenant/t1/workflows"):
        from flask import render_template

        html = render_template(
            "workflows.html",
            tenant=tenant,
            tenant_id="t1",
            media_buys=[pending_buy],
            tasks=[pending_task, completed_task],
            workflows=[pending_task, completed_task],
            audit_logs=[],
            summary={"active_buys": 1, "pending_tasks": 1, "completed_today": 0, "total_spend": 1000.0},
            script_name="",
        )
    return html


class TestMediaBuysTabApproveLink:
    """Issue #142 — link must target the detail page (GET), not /approve (POST)."""

    def test_review_approve_link_points_at_detail_page(self, rendered_workflows_html):
        html = rendered_workflows_html
        assert ">\n                                Review & Approve\n                            </a>" in html
        assert "/tenant/t1/media-buy/mb_pending" in html

    def test_review_approve_link_does_not_point_at_post_only_approve_endpoint(self, rendered_workflows_html):
        # The Media Buys row must not include the POST-only `/approve` URL.
        assert "/media-buy/mb_pending/approve" not in rendered_workflows_html


class TestTasksTabActionsColumn:
    """Issue #159 — Tasks tab must expose actions for every row."""

    def test_actions_column_header_present(self, rendered_workflows_html):
        assert "<th>Actions</th>" in rendered_workflows_html

    def test_pending_task_has_review_approve_button(self, rendered_workflows_html):
        html = rendered_workflows_html
        assert "/tenant/t1/workflows/ctx_001/steps/step_pending_001/review" in html
        # And it must be styled as primary.
        idx = html.index("step_pending_001/review")
        # Class attribute appears after the href on this anchor.
        window = html[idx : idx + 200]
        assert 'class="btn btn-sm btn-primary"' in window

    def test_completed_task_has_view_link_to_same_review_page(self, rendered_workflows_html):
        html = rendered_workflows_html
        assert "/tenant/t1/workflows/ctx_002/steps/step_done_001/review" in html
        idx = html.index("step_done_001/review")
        window = html[idx : idx + 200]
        assert 'class="btn btn-sm btn-link"' in window

    def test_context_column_renders_context_id(self, rendered_workflows_html):
        # Pre-fix the column referenced `task.workflow_id` which did not
        # exist on the dict, so it always rendered "-". The dict carries
        # `context_id`; the column now renders it.
        assert "<code>ctx_001</code>" in rendered_workflows_html
        assert "<code>ctx_002</code>" in rendered_workflows_html
