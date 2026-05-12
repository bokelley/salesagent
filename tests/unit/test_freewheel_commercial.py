"""Tests for the FreeWheel v3 commercial client.

Replays captured XML fixtures via an injected mock session. Fixtures live
in ``tests/fixtures/data/freewheel/v3/`` and were anonymised from a real
publisher's test network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

from src.adapters.freewheel._commercial import FreeWheelCommercialClient, _build_xml, _element_to_dict
from src.adapters.freewheel._transport import FreeWheelTransport

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "freewheel" / "v3"


def _make_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.ok = True
    mock.text = text
    mock.content = text.encode()
    return mock


def _replay(url_to_fixture: dict[str, Path]) -> FreeWheelCommercialClient:
    session = MagicMock()

    def fake_request(*, method, url, headers, data=None, timeout=None):
        path = url.split("?", 1)[0]
        for suffix, fixture in url_to_fixture.items():
            if path.endswith(suffix):
                return _make_response(fixture.read_text() if fixture else "")
        raise AssertionError(f"No fixture mapped for {url}")

    session.request.side_effect = fake_request
    return FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))


class TestAdvertiserListing:
    def test_list_advertisers_paginates(self):
        client = _replay({"/services/v3/advertisers": FIXTURES / "advertisers" / "list_page1.xml"})
        result = client.list_advertisers(per_page=10)
        assert result.total_count == 137
        assert result.total_page == 14
        assert len(result.items) == 10

    def test_get_advertiser_parses_detail(self):
        client = _replay({"/services/v3/advertisers/100002": FIXTURES / "advertisers" / "test_account_advertiser.xml"})
        advertiser = client.get_advertiser(100002)
        assert advertiser.id == 100002
        assert advertiser.status == "ACTIVE"


class TestCampaignListing:
    def test_list_campaigns_filters_by_advertiser(self):
        # Both URLs should be mapped — the filter still hits /campaigns
        client = _replay({"/services/v3/campaigns": FIXTURES / "campaigns" / "filtered_by_test_advertiser.xml"})
        result = client.list_campaigns(advertiser_id=100002)
        assert all(c.advertiser_id == 100002 for c in result.items if c.advertiser_id is not None)

    def test_get_campaign_parses_detail(self):
        client = _replay({"/services/v3/campaigns/91439758": FIXTURES / "campaigns" / "single.xml"})
        campaign = client.get_campaign(91439758)
        assert campaign.id == 91439758
        assert campaign.status == "IN_ACTIVE"


class TestCampaignCreate:
    """Covers the verified write path: POST /services/v3/campaign with the
    minimum required body (name + advertiser_id)."""

    def test_create_campaign_posts_xml_body(self):
        session = MagicMock()
        sample_response = (FIXTURES / "campaigns" / "single.xml").read_text()
        session.request.return_value = _make_response(sample_response)

        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))
        campaign = client.create_campaign(name="probe", advertiser_id=100002)

        call = session.request.call_args.kwargs
        assert call["method"] == "POST"
        assert call["url"].endswith("/services/v3/campaign")
        assert "<name>probe</name>" in call["data"]
        assert "<advertiser_id>100002</advertiser_id>" in call["data"]
        assert call["headers"]["Content-Type"] == "application/xml"
        # Sanity: parsed campaign came back populated
        assert campaign.id == 91439758

    def test_delete_campaign_uses_singular_path(self):
        session = MagicMock()
        session.request.return_value = _make_response("")
        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))

        client.delete_campaign(91439758)

        call = session.request.call_args.kwargs
        assert call["method"] == "DELETE"
        assert call["url"].endswith("/services/v3/campaign/91439758")

    def test_update_campaign_uses_put(self):
        """v3 update verb is PUT — PATCH returns 405."""
        session = MagicMock()
        session.request.return_value = _make_response(
            "<campaign><id>91439758</id><name>name 50</name><description>updated</description></campaign>"
        )
        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))

        updated = client.update_campaign(91439758, description="updated")

        call = session.request.call_args.kwargs
        assert call["method"] == "PUT"
        assert call["url"].endswith("/services/v3/campaign/91439758")
        assert "<description>updated</description>" in call["data"]
        # Fields not passed should NOT appear in the body — partial update.
        assert "<name>" not in call["data"]
        assert updated.description == "updated"


class TestInsertionOrderCreate:
    def test_create_io_posts_singular_path_with_campaign_id(self):
        session = MagicMock()
        session.request.return_value = _make_response(
            "<insertion_order><id>93935458</id><campaign_id>91439758</campaign_id>"
            "<name>io probe</name><stage>NOT_BOOKED</stage><currency>EUR</currency>"
            "</insertion_order>"
        )
        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))

        io = client.create_insertion_order(name="io probe", campaign_id=91439758)

        call = session.request.call_args.kwargs
        assert call["method"] == "POST"
        assert call["url"].endswith("/services/v3/insertion_order")
        assert "<name>io probe</name>" in call["data"]
        assert "<campaign_id>91439758</campaign_id>" in call["data"]
        assert io.id == 93935458
        assert io.campaign_id == 91439758
        assert io.currency == "EUR"

    def test_delete_io(self):
        session = MagicMock()
        session.request.return_value = _make_response("")
        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))

        client.delete_insertion_order(93935458)

        call = session.request.call_args.kwargs
        assert call["method"] == "DELETE"
        assert call["url"].endswith("/services/v3/insertion_order/93935458")


class TestPlacementCreate:
    def test_create_placement_posts_singular_path_with_io_id(self):
        session = MagicMock()
        session.request.return_value = _make_response(
            "<placement><id>93935461</id><insertion_order_id>93935458</insertion_order_id>"
            "<name>placement probe</name><status>IN_ACTIVE</status>"
            "<placement_type>NORMAL</placement_type></placement>"
        )
        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))

        placement = client.create_placement(name="placement probe", insertion_order_id=93935458)

        call = session.request.call_args.kwargs
        assert call["method"] == "POST"
        assert call["url"].endswith("/services/v3/placement")
        assert "<name>placement probe</name>" in call["data"]
        assert "<insertion_order_id>93935458</insertion_order_id>" in call["data"]
        assert placement.id == 93935461
        assert placement.placement_type == "NORMAL"

    def test_delete_placement(self):
        session = MagicMock()
        session.request.return_value = _make_response("")
        client = FreeWheelCommercialClient(FreeWheelTransport(api_token="t", session=session))

        client.delete_placement(93935461)

        call = session.request.call_args.kwargs
        assert call["method"] == "DELETE"
        assert call["url"].endswith("/services/v3/placement/93935461")


class TestInsertionOrders:
    def test_list_io_paginates(self):
        client = _replay({"/services/v3/insertion_orders": FIXTURES / "insertion_orders" / "list_page1.xml"})
        result = client.list_insertion_orders()
        assert result.total_page > 0

    def test_get_io_parses_budget_and_schedule(self):
        # IO single response reaches the advertiser via campaign_id, not
        # directly — advertiser_id is on the parent Campaign.
        client = _replay(
            {"/services/v3/insertion_orders/82421922": FIXTURES / "insertion_orders" / "single_test_advertiser.xml"}
        )
        io = client.get_insertion_order(82421922)
        assert io.id == 82421922
        assert io.campaign_id == 82421921
        assert io.currency == "EUR"
        assert io.budget is not None
        assert io.budget.budget_model == "IMPRESSION_TARGET"
        assert io.budget.impression == 10000
        assert io.schedule is None  # empty <schedule /> -> None via BeforeValidator


class TestPlacements:
    def test_list_placements_parse(self):
        client = _replay({"/services/v3/placements": FIXTURES / "placements" / "list_page1.xml"})
        result = client.list_placements()
        assert result.total_page > 0

    def test_get_placement_parses_detail_shape(self):
        # Single placement returns insertion_order_id + descriptive fields,
        # not the schedule that the list shape returns.
        client = _replay({"/services/v3/placements/90997225": FIXTURES / "placements" / "single.xml"})
        placement = client.get_placement(90997225)
        assert placement.id == 90997225
        assert placement.placement_type == "NORMAL"
        assert placement.insertion_order_id == 90763088


class TestXMLHelpers:
    def test_build_xml_writes_declaration_and_fields(self):
        body = _build_xml("campaign", {"name": "x", "advertiser_id": 1})
        assert body.startswith('<?xml version="1.0"')
        assert "<name>x</name>" in body
        assert "<advertiser_id>1</advertiser_id>" in body

    def test_build_xml_drops_none_values(self):
        body = _build_xml("campaign", {"name": "x", "description": None})
        assert "<description>" not in body

    def test_element_to_dict_preserves_nested_elements(self):
        root = ET.fromstring(
            "<io><id>1</id><budget><budget_model>X</budget_model><impression>5</impression></budget></io>"
        )
        result = _element_to_dict(root)
        assert result == {"id": "1", "budget": {"budget_model": "X", "impression": "5"}}

    def test_element_to_dict_empty_elements_become_empty_string(self):
        root = ET.fromstring("<io><id>1</id><schedule /></io>")
        result = _element_to_dict(root)
        assert result == {"id": "1", "schedule": ""}
