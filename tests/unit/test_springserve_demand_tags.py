"""Tests for SpringServeDemandTagsClient -- typed CRUD over /demand_tags."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._demand_tags import (
    SpringServeDemandTagsClient,
    _format_ss_datetime,
)
from src.adapters.springserve.entities import DemandTag


@pytest.fixture
def transport():
    return MagicMock()


@pytest.fixture
def client(transport):
    return SpringServeDemandTagsClient(transport)


def _demand_tag_response(demand_tag_id: int = 800001, **overrides) -> dict:
    body = {
        "id": demand_tag_id,
        "campaign_id": 900001,
        "account_id": 1730,
        "demand_partner_id": 88061,
        "name": "adcp_pkg",
        "active": False,
        "is_active": False,
        "rate_currency": "EUR",
        "cost_model_type": 0,
        "format": "video",
        "demand_tag_priorities": [],
        "budgets": [],
        "country_codes": [],
        "country_targeting": "All",
        "state_codes": [],
        "state_targeting": "All",
        "metro_area_codes": [],
        "metro_area_targeting": "All",
        "player_sizes": [],
        "player_size_targeting": "All",
        "user_agent_devices": [],
        "line_item_ratios": [],
    }
    body.update(overrides)
    return body


class TestDatetimeFormat:
    def test_naive_datetime_appends_z(self):
        result = _format_ss_datetime(datetime(2026, 2, 10, 0, 0, 0))
        assert result == "2026-02-10T00:00:00.000000Z"

    def test_aware_datetime_converted_to_utc_z(self):
        """SpringServe uses literal Z suffix for UTC. Aware datetimes get
        converted to UTC; the tzinfo is then dropped before formatting."""
        from datetime import timedelta, timezone

        eastern = timezone(timedelta(hours=-5))
        result = _format_ss_datetime(datetime(2026, 2, 10, 5, 0, 0, tzinfo=eastern))
        assert result == "2026-02-10T10:00:00.000000Z"


class TestCreate:
    def test_required_fields_with_defaults(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        start = datetime(2026, 6, 1, tzinfo=UTC)
        end = datetime(2026, 6, 30, tzinfo=UTC)

        result = client.create(
            name="adcp_pkg_1",
            campaign_id=900001,
            demand_partner_id=88061,
            start_date=start,
            end_date=end,
        )

        path, body = transport.post_json.call_args.args
        assert path == "/demand_tags"
        assert body["name"] == "adcp_pkg_1"
        assert body["campaign_id"] == 900001
        assert body["demand_partner_id"] == 88061
        assert body["start_date"] == "2026-06-01T00:00:00.000000Z"
        assert body["end_date"] == "2026-06-30T00:00:00.000000Z"
        assert body["format"] == "video"
        assert body["rate_currency"] == "USD"
        assert body["is_active"] is False
        assert isinstance(result, DemandTag)

    def test_rate_encoded_as_string(self, client, transport):
        """SpringServe stores rate as a string -- the client coerces."""
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            rate=27.0,
        )
        body = transport.post_json.call_args.args[1]
        assert body["rate"] == "27.0"

    def test_country_targeting_implied_white_list_when_codes_present(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            country_codes=["NL", "BE"],
        )
        body = transport.post_json.call_args.args[1]
        assert body["country_codes"] == ["NL", "BE"]
        assert body["country_targeting"] == "White List"  # auto-flipped

    def test_audio_format_passthrough(self, client, transport):
        transport.post_json.return_value = _demand_tag_response(format="audio")
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            format="audio",
        )
        body = transport.post_json.call_args.args[1]
        assert body["format"] == "audio"

    def test_demand_tag_priorities_pass_through(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        priorities = [{"supply_tag_id": 945522, "priority": 1, "tier": 1}]
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            demand_tag_priorities=priorities,
        )
        body = transport.post_json.call_args.args[1]
        assert body["demand_tag_priorities"] == priorities

    def test_extras_kwargs_merged(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            skip_enabled=True,
            timeout=5000,
        )
        body = transport.post_json.call_args.args[1]
        assert body["skip_enabled"] is True
        assert body["timeout"] == 5000


class TestGet:
    def test_returns_typed_demand_tag(self, client, transport):
        transport.get_json.return_value = _demand_tag_response(800042)
        result = client.get(800042)
        transport.get_json.assert_called_once_with("/demand_tags/800042")
        assert result.id == 800042


class TestUpdate:
    def test_is_active_toggle(self, client, transport):
        transport.put_json.return_value = _demand_tag_response(is_active=True)
        client.update(800001, is_active=True)
        transport.put_json.assert_called_once_with("/demand_tags/800001", {"is_active": True})

    def test_arbitrary_fields_pass_through(self, client, transport):
        transport.put_json.return_value = _demand_tag_response()
        client.update(800001, rate="30.0", end_date="2026-07-31T00:00:00.000000Z")
        body = transport.put_json.call_args.args[1]
        assert body == {"rate": "30.0", "end_date": "2026-07-31T00:00:00.000000Z"}


class TestDelete:
    def test_delete_calls_delete_json(self, client, transport):
        client.delete(800001)
        transport.delete_json.assert_called_once_with("/demand_tags/800001")
