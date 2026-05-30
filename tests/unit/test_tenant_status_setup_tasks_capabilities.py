"""Setup-tasks projection respects EMBEDDED_CAPABILITIES on managed instances.

The /status setup_tasks block historically only knew two special keys
(``public_agent_url`` → platform scope, ``authorized_properties`` → hidden).
Everything else fell through to publisher scope — including cosmetic items
like Slack, Gemini, and Creative Approval, whose UIs are gated off by
``{% if publisher_owns(...) %}`` when the storefront owns the workflow.
That produced action items the seller couldn't fix.

These tests pin the contract: when ``MANAGED_INSTANCE=true`` and
``EMBEDDED_CAPABILITIES`` hands a workflow to the storefront, the matching
setup task disappears from the publisher-facing /status payload (same
treatment as ``public_agent_url`` on managed tenants).

No ``TenantStatusEnv`` harness exists (see tests/CLAUDE.md). ``_setup_tasks_block``
is a pure projection over one ORM read + one service call, so a dedicated harness
env would be overkill; tests stub both inputs directly via ``SimpleNamespace`` +
``patch``, matching the pattern in ``test_setup_checklist_aao_resolved_url.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.admin.services.tenant_status_service import _setup_tasks_block


def _checklist_with_capability_tasks() -> dict:
    """Minimal SetupChecklistService.get_setup_status() payload covering
    every key in ``_TASK_CAPABILITY`` plus one unrelated task so we can
    confirm non-capability items are unaffected."""
    return {
        "progress_percent": 0,
        "completed_count": 0,
        "total_count": 6,
        "ready_for_orders": False,
        "critical": [
            {
                "key": "currency_limits",
                "name": "Currency Configuration",
                "description": "At least one currency must be configured",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#business-rules",
                "details": "No currencies configured",
            },
        ],
        "recommended": [
            {
                "key": "gam_advertiser_create_permission",
                "name": "GAM Advertiser Create Permission",
                "description": "Prove the configured GAM credential can create advertiser companies",
                "is_complete": False,
                "action_url": "/tenant/t_managed/buyer-routing",
                "details": "Run advertiser ensure",
            },
            {
                "key": "slack_integration",
                "name": "Slack Integration",
                "description": "Configure Slack webhooks for order notifications",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#integrations",
                "details": "No Slack integration",
            },
            {
                "key": "creative_approval_guidelines",
                "name": "Creative Approval Guidelines",
                "description": "Configure auto-approval rules",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#policies",
                "details": "Using default (manual review required)",
            },
        ],
        "optional": [
            {
                "key": "gemini_api_key",
                "name": "Gemini AI Features",
                "description": "Enable AI-assisted recommendations",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#integrations",
                "details": "Optional: Configure Gemini API key",
            },
            {
                "key": "signals_agent",
                "name": "Signals Discovery Agent",
                "description": "Enable AXE signals for advanced targeting",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#integrations",
                "details": "AXE signals not configured",
            },
        ],
    }


def _checklist_with_embedded_irrelevant_tasks() -> dict:
    """Minimal setup payload for tasks that are valid on open instances but
    should not be sent to embedded publishers."""
    return {
        "progress_percent": 0,
        "completed_count": 0,
        "total_count": 6,
        "ready_for_orders": True,
        "critical": [
            {
                "key": "currency_limits",
                "name": "Currency Configuration",
                "description": "At least one currency must be configured",
                "is_complete": True,
                "action_url": "/tenant/t_managed/settings#business-rules",
                "details": "1 currencies configured",
            },
        ],
        "recommended": [
            {
                "key": "naming_conventions",
                "name": "Naming Conventions",
                "description": "Customize order and line item naming templates",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#business-rules",
                "details": "Using default naming templates",
            },
            {
                "key": "budget_controls",
                "name": "Budget Controls",
                "description": "Set maximum daily budget limits for safety",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#business-rules",
                "details": "Budget limits can be set per currency",
            },
            {
                "key": "tenant_cname",
                "name": "Custom Domain (CNAME)",
                "description": "Configure custom domain for your sales agent",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#account",
                "details": "Using default subdomain",
            },
        ],
        "optional": [
            {
                "key": "sso_configuration",
                "name": "Single Sign-On (SSO)",
                "description": "Configure tenant-specific SSO authentication",
                "is_complete": False,
                "action_url": "/tenant/t_managed/users",
                "details": "SSO not configured",
            },
            {
                "key": "multiple_currencies",
                "name": "Multiple Currencies",
                "description": "Support international advertisers with EUR, GBP, etc.",
                "is_complete": False,
                "action_url": "/tenant/t_managed/settings#business-rules",
                "details": "Only 1 currency configured",
            },
        ],
    }


def _make_session(tenant):
    """Stand-in for the sqlalchemy Session shape ``_setup_tasks_block`` consumes:
    ``session.scalars(stmt).first()`` returning a Tenant row."""

    class _Result:
        def first(self):
            return tenant

    class _Session:
        def scalars(self, _stmt):
            return _Result()

    return _Session()


@pytest.fixture
def fake_session():
    """Session whose Tenant lookup returns a managed-instance row."""
    return _make_session(SimpleNamespace(tenant_id="t_managed", is_embedded=True))


@pytest.fixture
def open_session():
    """Session for an open-instance (non-embedded) tenant."""
    return _make_session(SimpleNamespace(tenant_id="t_open", is_embedded=False))


def _ids(block) -> set[str]:
    return {item.id for item in block.items}


class TestStorefrontOwnedTasksAreSuppressedOnManaged:
    """``MANAGED_INSTANCE=true`` + storefront-owned capability → task hidden."""

    def test_slack_owned_by_storefront_drops_slack_task(self, monkeypatch, fake_session):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "storefront"}')

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_capability_tasks(),
        ):
            block = _setup_tasks_block(fake_session, "t_managed")

        ids = _ids(block)
        assert "gam_advertiser_create_permission" not in ids
        assert "slack_integration" not in ids
        # Other capability-gated items still appear (their capabilities aren't claimed).
        assert "creative_approval_guidelines" in ids
        assert "gemini_api_key" in ids
        assert "signals_agent" in ids
        # Non-capability item is unaffected.
        assert "currency_limits" in ids

    def test_all_capabilities_owned_by_storefront_drops_all_four(self, monkeypatch, fake_session):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv(
            "EMBEDDED_CAPABILITIES",
            '{"slack": "storefront", "ai_services": "storefront", '
            '"creative_approval": "storefront", "signals_agents": "storefront"}',
        )

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_capability_tasks(),
        ):
            block = _setup_tasks_block(fake_session, "t_managed")

        ids = _ids(block)
        assert "gam_advertiser_create_permission" not in ids
        assert "slack_integration" not in ids
        assert "creative_approval_guidelines" not in ids
        assert "gemini_api_key" not in ids
        assert "signals_agent" not in ids
        # The unrelated currency item must still be there — we didn't accidentally
        # over-filter the publisher-scope tasks.
        assert ids == {"currency_limits"}

    def test_blocker_and_warning_counts_drop_when_items_suppressed(self, monkeypatch, fake_session):
        """The recommended items are warnings; suppressing them must shrink
        warning_count so Storefront's badge doesn't lie."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv(
            "EMBEDDED_CAPABILITIES",
            '{"slack": "storefront", "creative_approval": "storefront"}',
        )

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_capability_tasks(),
        ):
            block = _setup_tasks_block(fake_session, "t_managed")

        # Only currency_limits remains in critical tier → 1 blocker.
        assert block.blocker_count == 1
        # Warning-tier publisher items (Slack, Creative Approval) and the
        # platform-owned GAM proof task are suppressed → 0 warnings.
        assert block.warning_count == 0


class TestPublisherOwnedTasksRemain:
    """When the publisher still owns the capability, the task must appear."""

    def test_publisher_explicit_keeps_task_visible(self, monkeypatch, fake_session):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "publisher"}')

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_capability_tasks(),
        ):
            block = _setup_tasks_block(fake_session, "t_managed")

        assert "slack_integration" in _ids(block)

    def test_capability_unset_defaults_to_publisher(self, monkeypatch, fake_session):
        """Unknown capability key defaults to publisher (per embedded_capabilities
        contract). The setup task must stay visible to the seller."""
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_capability_tasks(),
        ):
            block = _setup_tasks_block(fake_session, "t_managed")

        ids = _ids(block)
        assert {"slack_integration", "creative_approval_guidelines", "gemini_api_key", "signals_agent"} <= ids


class TestOpenInstanceIgnoresCapabilities:
    """Open instances have no storefront. EMBEDDED_CAPABILITIES is irrelevant."""

    def test_open_instance_keeps_tasks_even_with_storefront_capabilities(self, monkeypatch, open_session):
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        monkeypatch.setenv(
            "EMBEDDED_CAPABILITIES",
            '{"slack": "storefront", "ai_services": "storefront"}',
        )

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_capability_tasks(),
        ):
            block = _setup_tasks_block(open_session, "t_open")

        ids = _ids(block)
        # Every non-hidden task surfaces to the open-instance publisher.
        assert {"slack_integration", "creative_approval_guidelines", "gemini_api_key", "signals_agent"} <= ids
        # All items carry publisher scope on open instances.
        assert all(item.scope == "publisher" for item in block.items)


class TestManagedOnlyTasksAreHidden:
    """Embedded mode suppresses tasks that the publisher cannot act on."""

    def test_embedded_irrelevant_tasks_are_not_sent_to_managed_publishers(self, monkeypatch, fake_session):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_embedded_irrelevant_tasks(),
        ):
            block = _setup_tasks_block(fake_session, "t_managed")

        ids = _ids(block)
        assert "budget_controls" not in ids
        assert "tenant_cname" not in ids
        assert "sso_configuration" not in ids
        assert "multiple_currencies" not in ids
        # Still publisher-actionable in embedded mode.
        assert ids == {"currency_limits", "naming_conventions"}
        assert block.warning_count == 1

    def test_open_instance_keeps_tasks_that_are_only_hidden_for_managed(self, monkeypatch, open_session):
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)

        with patch(
            "src.services.setup_checklist_service.SetupChecklistService.get_setup_status",
            return_value=_checklist_with_embedded_irrelevant_tasks(),
        ):
            block = _setup_tasks_block(open_session, "t_open")

        ids = _ids(block)
        assert {
            "budget_controls",
            "tenant_cname",
            "sso_configuration",
            "multiple_currencies",
        } <= ids
        assert all(item.scope == "publisher" for item in block.items)
