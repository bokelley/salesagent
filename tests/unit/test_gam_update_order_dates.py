"""Tests for `GAMOrdersManager.update_order_dates`.

Covers tescoboy issue #157: `_update_media_buy_impl` previously wrote new
flight bounds to Postgres only and emitted a TODO instead of pushing the
change to GAM. Approved updates left the DB and ad server permanently
out of sync. The fix adds `update_order_dates` to the GAM adapter and
wires it into the impl after the DB write.

These tests target the adapter helper directly using a mocked GAM
client. Wire-shape assertions live in `test_gam_payload_shape.py`; here
we focus on the call surface and partial-failure semantics.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from src.adapters.gam.managers.orders import GAMOrdersManager


def _make_manager(client_manager=None, dry_run=False):
    return GAMOrdersManager(
        client_manager=client_manager,
        advertiser_id="adv_1",
        trafficker_id="tr_1",
        dry_run=dry_run,
    )


def _client_with_services(order_results, line_item_results, update_orders_result, update_lis_result):
    order_service = MagicMock()
    order_service.getOrdersByStatement.return_value = {"results": order_results}
    order_service.updateOrders.return_value = update_orders_result

    lis_service = MagicMock()
    lis_service.getLineItemsByStatement.return_value = {"results": line_item_results}
    lis_service.updateLineItems.return_value = update_lis_result

    client_manager = MagicMock()
    client_manager.get_service.side_effect = lambda name: {
        "OrderService": order_service,
        "LineItemService": lis_service,
    }[name]
    return client_manager, order_service, lis_service


class TestUpdateOrderDatesDryRun:
    def test_dry_run_skips_gam_calls(self):
        client_manager = MagicMock()
        manager = _make_manager(client_manager=client_manager, dry_run=True)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        )

        assert ok is True
        client_manager.get_service.assert_not_called()


class TestUpdateOrderDatesNoOp:
    def test_no_dates_returns_true_without_calling_gam(self):
        client_manager = MagicMock()
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(order_id="123", start_time=None, end_time=None)

        assert ok is True
        client_manager.get_service.assert_not_called()


class TestUpdateOrderDatesSuccess:
    def test_patches_order_and_all_line_items(self):
        order = {"id": 123, "name": "Order"}
        li_a = {"id": 1, "name": "LI-A"}
        li_b = {"id": 2, "name": "LI-B"}
        client_manager, order_service, lis_service = _client_with_services(
            order_results=[order],
            line_item_results=[li_a, li_b],
            update_orders_result=[order],
            update_lis_result=[li_a, li_b],
        )
        manager = _make_manager(client_manager=client_manager)

        start = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)  # 11:00 EDT
        end = datetime(2026, 5, 14, 15, 0, tzinfo=UTC)
        ok = manager.update_order_dates(order_id="123", start_time=start, end_time=end)

        assert ok is True
        # Order was patched with tz-converted wall-clock fields. Order has
        # no timeZoneId field in GAM; only LineItems carry it.
        assert order["startDateTime"]["hour"] == 11
        assert order["startDateTime"]["date"] == {"year": 2026, "month": 5, "day": 7}
        assert "timeZoneId" not in order["startDateTime"]
        order_service.updateOrders.assert_called_once_with([order])

        # Both line items patched and updateLineItems called once with the batch.
        assert li_a["startDateTime"]["timeZoneId"] == "America/New_York"
        assert li_b["startDateTime"]["timeZoneId"] == "America/New_York"
        assert li_a["endDateTime"]["hour"] == 11
        lis_service.updateLineItems.assert_called_once_with([li_a, li_b])

    def test_only_end_time_supplied_leaves_start_unchanged(self):
        order = {"id": 123, "startDateTime": "ORIGINAL"}
        li = {"id": 1, "startDateTime": "ORIGINAL"}
        client_manager, *_ = _client_with_services(
            order_results=[order],
            line_item_results=[li],
            update_orders_result=[order],
            update_lis_result=[li],
        )
        manager = _make_manager(client_manager=client_manager)

        end = datetime(2026, 5, 14, 15, 0, tzinfo=UTC)
        ok = manager.update_order_dates(order_id="123", start_time=None, end_time=end)

        assert ok is True
        assert order["startDateTime"] == "ORIGINAL"
        assert order["endDateTime"]["date"] == {"year": 2026, "month": 5, "day": 14}
        assert li["startDateTime"] == "ORIGINAL"
        assert li["endDateTime"]["hour"] == 11

    def test_order_with_no_line_items_still_succeeds(self):
        order = {"id": 123}
        client_manager, _, lis_service = _client_with_services(
            order_results=[order],
            line_item_results=[],
            update_orders_result=[order],
            update_lis_result=[],
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )

        assert ok is True
        lis_service.updateLineItems.assert_not_called()


class TestUpdateOrderDatesFailure:
    def test_order_not_found_returns_false(self):
        client_manager, *_ = _client_with_services(
            order_results=[],
            line_item_results=[],
            update_orders_result=None,
            update_lis_result=None,
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_update_orders_returns_empty_returns_false(self):
        order = {"id": 123}
        client_manager, *_ = _client_with_services(
            order_results=[order],
            line_item_results=[{"id": 1}],
            update_orders_result=None,
            update_lis_result=[{"id": 1}],
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_partial_line_item_update_returns_false(self):
        order = {"id": 123}
        client_manager, _, lis_service = _client_with_services(
            order_results=[order],
            line_item_results=[{"id": 1}, {"id": 2}],
            update_orders_result=[order],
            update_lis_result=[{"id": 1}],  # only 1 of 2 echoed back
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_get_orders_raises_returns_false(self):
        client_manager = MagicMock()
        order_service = MagicMock()
        order_service.getOrdersByStatement.side_effect = RuntimeError("network down")
        client_manager.get_service.return_value = order_service
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_line_item_update_raises_returns_false(self):
        order = {"id": 123}
        order_service = MagicMock()
        order_service.getOrdersByStatement.return_value = {"results": [order]}
        order_service.updateOrders.return_value = [order]

        lis_service = MagicMock()
        lis_service.getLineItemsByStatement.side_effect = RuntimeError("forecast error")

        client_manager = MagicMock()
        client_manager.get_service.side_effect = lambda name: {
            "OrderService": order_service,
            "LineItemService": lis_service,
        }[name]
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False
