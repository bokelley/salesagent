"""GAM sandbox trafficking behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from src.adapters.gam.managers.orders import GAMOrdersManager


class _LineItemService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def createLineItems(self, line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.created.extend(line_items)
        return [{"id": 98765}]


class _ClientManager:
    def __init__(self, line_item_service: _LineItemService) -> None:
        self.line_item_service = line_item_service

    def get_service(self, service_name: str) -> _LineItemService:
        assert service_name == "LineItemService"
        return self.line_item_service


def test_sandbox_trafficking_creates_house_zero_cpm_line_item():
    service = _LineItemService()
    manager = GAMOrdersManager(
        client_manager=_ClientManager(service),
        advertiser_id="12345",
        trafficker_id="67890",
        dry_run=False,
    )
    package = SimpleNamespace(
        package_id="pkg_sandbox",
        product_id="prod_sandbox",
        name="Sandbox Package",
        impressions=0,
        format_ids=[],
        targeting_overlay=None,
        creative_ids=None,
    )

    created_ids = manager.create_line_items(
        order_id="111",
        packages=[package],
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 1, 3, tzinfo=UTC),
        products_map={
            "pkg_sandbox": {
                "product_id": "prod_sandbox",
                "delivery_type": "guaranteed",
                "implementation_config": {"targeted_ad_unit_ids": ["123456"]},
            }
        },
        package_pricing_info={
            "pkg_sandbox": {
                "pricing_model": "cpm",
                "rate": 0.0,
                "currency": "USD",
                "is_fixed": True,
                "bid_price": None,
                "sandbox_trafficking": True,
            }
        },
    )

    assert created_ids == ["98765"]
    line_item = service.created[0]
    assert line_item["lineItemType"] == "HOUSE"
    assert line_item["priority"] == 16
    assert line_item["costType"] == "CPM"
    assert line_item["costPerUnit"] == {"currencyCode": "USD", "microAmount": 0}
    assert line_item["primaryGoal"] == {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100}
