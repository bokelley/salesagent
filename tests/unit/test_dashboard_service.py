"""Tests for DashboardService using single data source pattern."""

# ruff: noqa: PLR0913

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from src.admin.services.dashboard_service import DashboardService
from src.core.database.models import Tenant


class TestDashboardService:
    """Test DashboardService single data source pattern."""

    def test_init_validates_tenant_id(self):
        """Test that invalid tenant IDs are rejected."""
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            DashboardService("")

        with pytest.raises(ValueError, match="Invalid tenant_id"):
            DashboardService("x" * 51)  # Too long

    def test_init_valid_tenant_id(self):
        """Test that valid tenant IDs are accepted."""
        service = DashboardService("test_tenant")
        assert service.tenant_id == "test_tenant"
        assert service._tenant is None  # Not loaded yet

    @patch("src.admin.services.dashboard_service.get_db_session")
    def test_get_tenant_caches_result(self, mock_get_db):
        """Test that tenant is cached after first load."""
        # Mock database session
        mock_session = Mock()
        mock_get_db.return_value.__enter__.return_value = mock_session

        # Mock tenant (SQLAlchemy 2.0 pattern)
        mock_tenant = Mock(spec=Tenant)
        mock_tenant.tenant_id = "test_tenant"
        mock_scalars = Mock()
        mock_scalars.first.return_value = mock_tenant
        mock_session.scalars.return_value = mock_scalars

        service = DashboardService("test_tenant")

        # First call should query database
        result1 = service.get_tenant()
        assert result1 == mock_tenant
        assert service._tenant == mock_tenant

        # Second call should use cache
        result2 = service.get_tenant()
        assert result2 == mock_tenant

        # Should only have called database once
        mock_session.scalars.assert_called_once()

    @patch("src.admin.services.dashboard_service.MediaBuyReadinessService")
    @patch("src.admin.services.dashboard_service.get_db_session")
    @patch("src.admin.services.dashboard_service.get_business_activities")
    def test_get_dashboard_metrics_single_data_source(self, mock_get_activities, mock_get_db, mock_readiness_service):
        """Test that dashboard metrics use single data source pattern."""
        # Mock database session
        mock_session = Mock()
        mock_get_db.return_value.__enter__.return_value = mock_session

        # Mock tenant
        mock_tenant = Mock(spec=Tenant)
        mock_tenant.tenant_id = "test_tenant"

        # Mock SQLAlchemy 2.0 query results
        mock_scalars = Mock()
        mock_scalars.all.return_value = []
        mock_session.scalars.return_value = mock_scalars
        mock_session.scalar.return_value = 5  # For count queries

        # Mock readiness summary
        mock_readiness_summary = {
            "live": 2,
            "scheduled": 1,
            "needs_creatives": 1,
            "needs_approval": 0,
            "paused": 0,
            "completed": 3,
            "failed": 0,
            "draft": 0,
        }
        mock_readiness_service.get_tenant_readiness_summary.return_value = mock_readiness_summary

        # Mock recent activities (SINGLE DATA SOURCE)
        mock_activities = [{"operation": "test", "success": True}]
        mock_get_activities.return_value = mock_activities

        service = DashboardService("test_tenant")
        service._tenant = mock_tenant  # Skip tenant lookup

        metrics = service.get_dashboard_metrics()

        # Verify single data source pattern
        assert metrics["recent_activity"] == mock_activities
        mock_get_activities.assert_called_once_with("test_tenant", limit=10)

        # Verify workflow metrics are hardcoded (no database dependency)
        assert metrics["pending_workflows"] == 0
        assert metrics["approval_needed"] == 0
        assert metrics["pending_approvals"] == 0

        # Verify business metrics are calculated with new readiness states
        assert "total_revenue" in metrics
        assert "live_buys" in metrics
        assert "scheduled_buys" in metrics
        assert "needs_attention" in metrics
        assert "readiness_summary" in metrics

    # Note: Complex eager loading test moved to integration suite for better database testing

    def test_calculate_revenue_change(self):
        """Test revenue change calculation logic."""
        service = DashboardService("test_tenant")

        # Test with sufficient data (14 days)
        revenue_data = [{"revenue": 100} for _ in range(14)]  # Flat revenue
        change = service._calculate_revenue_change(revenue_data)
        assert change == 0.0  # No change

        # Test with growth
        revenue_data = [{"revenue": 50} for _ in range(7)] + [{"revenue": 100} for _ in range(7)]
        change = service._calculate_revenue_change(revenue_data)
        assert change == 100.0  # 100% increase

        # Test with insufficient data
        revenue_data = [{"revenue": 100} for _ in range(5)]
        change = service._calculate_revenue_change(revenue_data)
        assert change == 0.0

    def test_get_chart_data_format(self):
        """Test that chart data is formatted correctly for frontend."""
        service = DashboardService("test_tenant")

        # Mock the get_dashboard_metrics method
        mock_revenue_data = [{"date": "2025-01-01", "revenue": 100}, {"date": "2025-01-02", "revenue": 150}]

        with patch.object(service, "get_dashboard_metrics") as mock_metrics:
            mock_metrics.return_value = {"revenue_data": mock_revenue_data}

            chart_data = service.get_chart_data()

            assert chart_data["labels"] == ["2025-01-01", "2025-01-02"]
            assert chart_data["data"] == [100, 150]

    @patch("src.admin.services.dashboard_service.get_db_session")
    @patch("src.admin.services.dashboard_service.get_business_activities")
    def test_health_check_healthy(self, mock_get_activities, mock_get_db):
        """Test health check when system is healthy."""
        # Mock successful database connection
        mock_session = Mock()
        mock_get_db.return_value.__enter__.return_value = mock_session
        mock_session.execute.return_value.scalar.return_value = 1

        # Mock successful activity fetch
        mock_get_activities.return_value = []

        health = DashboardService.health_check()

        assert health["status"] == "healthy"
        assert health["single_data_source"] == "audit_logs"
        assert "tasks" in health["deprecated_sources"]
        assert "human_tasks" in health["deprecated_sources"]

    def test_needs_attention_items_carry_urls(self):
        """Each attention-rail item must include a ``url`` so the dashboard
        can wrap the row in an anchor — the rail is the operator's entry
        point into the queue, so every actionable row leads somewhere.

        Covers the IA fix for Configure-menu / ops-queue confusion: pending
        creatives → /creatives/review; single expiring → that buy's detail;
        multiple expiring → media-buys list; pacing-under → that buy.
        """
        service = DashboardService("test_tenant")

        mock_session = Mock()
        # Pending creatives count
        mock_session.scalar.return_value = 3

        # Two active buys: one expiring today (single), one pacing under
        today = datetime.now(UTC).date()
        expiring_buy = Mock()
        expiring_buy.media_buy_id = "buy_expiring"
        expiring_buy.advertiser_name = "Acme"
        expiring_buy.end_date = today
        expiring_buy.is_paused = False

        pacing_buy = Mock()
        pacing_buy.media_buy_id = "buy_pacing"
        pacing_buy.advertiser_name = "Globex"
        pacing_buy.end_date = today + timedelta(days=30)  # not expiring
        pacing_buy.is_paused = False

        mock_repo = Mock()
        mock_repo.list_by_statuses.return_value = [expiring_buy, pacing_buy]

        # _running_row decides pacing — mark expiring as on-pace, pacing_buy as under
        def fake_running_row(buy, _now):
            if buy is pacing_buy:
                return {"pacing": "under", "delivery_pct": 0.4, "flight_pct": 0.7}
            return {"pacing": "ok", "delivery_pct": 0.5, "flight_pct": 0.5}

        with patch.object(service, "_running_row", side_effect=fake_running_row):
            items = service._needs_attention(mock_session, mock_repo)

        urls = [item.get("url") for item in items]
        # Pending creatives → review queue
        assert "/tenant/test_tenant/creatives/review" in urls
        # Single expiring buy → deep-link to its detail page (singular
        # /media-buy/<id> — registered by operations_bp).
        assert "/tenant/test_tenant/media-buy/buy_expiring" in urls
        # Pacing-under buy → deep-link to its detail page.
        assert "/tenant/test_tenant/media-buy/buy_pacing" in urls

    def test_needs_attention_multiple_expiring_links_to_list(self):
        """Multiple deals expiring in the same window collapse into a single
        rail row; the link goes to the filtered media-buys list rather than
        an arbitrary buy detail."""
        service = DashboardService("test_tenant")

        mock_session = Mock()
        mock_session.scalar.return_value = 0  # no pending creatives

        today = datetime.now(UTC).date()
        buys = []
        for i in range(3):
            b = Mock()
            b.media_buy_id = f"buy_{i}"
            b.advertiser_name = f"Adv {i}"
            b.end_date = today
            b.is_paused = False
            buys.append(b)

        mock_repo = Mock()
        mock_repo.list_by_statuses.return_value = buys

        with patch.object(
            service,
            "_running_row",
            return_value={"pacing": "ok", "delivery_pct": 0.5, "flight_pct": 0.5},
        ):
            items = service._needs_attention(mock_session, mock_repo)

        expiring_items = [i for i in items if "expiring" in i["title"]]
        assert len(expiring_items) == 1
        assert expiring_items[0]["url"] == "/tenant/test_tenant/media-buys?status=live"

    def test_needs_attention_empty_state_has_no_url(self):
        """The fallback "nothing needs your attention" row is informational —
        it should not render as a link, so it omits the ``url`` field
        (template renders unwrapped row when url is falsy/missing)."""
        service = DashboardService("test_tenant")

        mock_session = Mock()
        mock_session.scalar.return_value = 0

        mock_repo = Mock()
        mock_repo.list_by_statuses.return_value = []

        items = service._needs_attention(mock_session, mock_repo)
        assert len(items) == 1
        assert items[0].get("url") is None

    @patch("src.admin.services.dashboard_service.get_db_session")
    def test_health_check_unhealthy(self, mock_get_db):
        """Test health check when system is unhealthy."""
        # Mock database connection failure
        mock_get_db.side_effect = Exception("Database connection failed")

        health = DashboardService.health_check()

        assert health["status"] == "unhealthy"
        assert "Database connection failed" in health["error"]
