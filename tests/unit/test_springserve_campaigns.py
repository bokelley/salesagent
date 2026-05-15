"""Tests for SpringServeCampaignsClient -- typed CRUD over /campaigns."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._campaigns import SpringServeCampaignsClient
from src.adapters.springserve.entities import Campaign


@pytest.fixture
def transport():
    return MagicMock()


@pytest.fixture
def client(transport):
    return SpringServeCampaignsClient(transport)


def _campaign_response(campaign_id: int = 900001, **overrides) -> dict:
    """Build a representative SpringServe Campaign response body."""
    body = {
        "id": campaign_id,
        "account_id": 1730,
        "demand_partner_id": 88061,
        "name": "adcp_smoke_test",
        "is_active": False,
        "is_managed": False,
        "rate_currency": "EUR",
        "cost_model_type": 0,
        "demand_tag_ids": [],
        "guaranteed_delivery": False,
    }
    body.update(overrides)
    return body


class TestCreate:
    def test_required_fields_only(self, client, transport):
        transport.post_json.return_value = _campaign_response()
        result = client.create(name="adcp_smoke_test", demand_partner_id=88061)

        transport.post_json.assert_called_once_with(
            "/campaigns",
            {
                "name": "adcp_smoke_test",
                "demand_partner_id": 88061,
                "is_active": False,
                "rate_currency": "USD",
                "cost_model_type": 0,
            },
        )
        assert isinstance(result, Campaign)
        assert result.id == 900001
        assert result.demand_partner_id == 88061

    def test_optional_fields_included_when_provided(self, client, transport):
        transport.post_json.return_value = _campaign_response()
        client.create(
            name="adcp_smoke_test",
            demand_partner_id=88061,
            code="PO-1234",
            secondary_code="adcp_PO-1234",
            note="AdCP MediaBuy",
            rate_currency="EUR",
            is_active=True,
        )
        body = transport.post_json.call_args.args[1]
        assert body["code"] == "PO-1234"
        assert body["secondary_code"] == "adcp_PO-1234"
        assert body["note"] == "AdCP MediaBuy"
        assert body["rate_currency"] == "EUR"
        assert body["is_active"] is True

    def test_optional_fields_omitted_when_none(self, client, transport):
        transport.post_json.return_value = _campaign_response()
        client.create(name="x", demand_partner_id=1)
        body = transport.post_json.call_args.args[1]
        assert "code" not in body
        assert "secondary_code" not in body
        assert "note" not in body


class TestGet:
    def test_returns_typed_campaign(self, client, transport):
        transport.get_json.return_value = _campaign_response(900042, name="other")
        result = client.get(900042)
        transport.get_json.assert_called_once_with("/campaigns/900042")
        assert result.id == 900042
        assert result.name == "other"


class TestUpdate:
    def test_is_active_toggle(self, client, transport):
        transport.put_json.return_value = _campaign_response(is_active=True)
        client.update(900001, is_active=True)
        transport.put_json.assert_called_once_with("/campaigns/900001", {"is_active": True})

    def test_arbitrary_field_passthrough(self, client, transport):
        transport.put_json.return_value = _campaign_response()
        client.update(900001, note="updated", code="new_code")
        body = transport.put_json.call_args.args[1]
        assert body == {"note": "updated", "code": "new_code"}


class TestDelete:
    def test_delete_calls_delete_json(self, client, transport):
        client.delete(900001)
        transport.delete_json.assert_called_once_with("/campaigns/900001")


class TestCampaignEntity:
    def test_extra_fields_round_trip(self):
        """SS responses carry 38+ fields; we model only the ones we care about
        and let everything else round-trip via extra='allow'."""
        body = _campaign_response()
        body["targeting_geo_profile"] = {"id": 240520, "country_codes": []}  # unmodelled
        campaign = Campaign.model_validate(body)
        dumped = campaign.model_dump()
        assert dumped["targeting_geo_profile"] == {"id": 240520, "country_codes": []}

    def test_string_rate_preserved(self):
        """SpringServe encodes rate as a string ('27.0') -- entity preserves it."""
        body = _campaign_response()
        body["rate"] = "27.0"
        campaign = Campaign.model_validate(body)
        assert campaign.rate == "27.0"
