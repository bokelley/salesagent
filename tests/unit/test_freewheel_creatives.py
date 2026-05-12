"""Tests for the FreeWheel v4 creative resources client.

Replays captured JSON fixtures via an injected mock session. Fixtures live
in ``tests/fixtures/data/freewheel/v4/creative_resources/`` (anonymised
from a real publisher's test network).
"""

from __future__ import annotations

from pathlib import Path

from src.adapters.freewheel._creatives import FreeWheelCreativeClient
from src.adapters.freewheel._transport import FreeWheelTransport
from tests.helpers.freewheel_replay import replay_session

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "freewheel" / "v4" / "creative_resources"


def _replay(url_to_fixture: dict[str, Path]) -> FreeWheelCreativeClient:
    return FreeWheelCreativeClient(FreeWheelTransport(api_token="t", session=replay_session(url_to_fixture)))


class TestCreativeListing:
    def test_list_returns_paginated_envelope(self):
        client = _replay({"/services/v4/creative_resources": FIXTURES / "list_page1.json"})
        result = client.list_creatives(per_page=10)

        # creative_resources uses ``total`` + ``total_pages`` field names —
        # our PaginatedResponse accepts them via AliasChoices, so the
        # canonical model fields are populated.
        assert result.total_count == 70
        assert result.total_page == 7
        assert len(result.items) == 10

    def test_list_items_parse_as_creatives(self):
        client = _replay({"/services/v4/creative_resources": FIXTURES / "list_page1.json"})
        result = client.list_creatives()

        first = result.items[0]
        assert first.id == 2427707
        assert first.base_ad_unit == "video"
        assert first.status == "ACTIVE"
        # advertiser_ids comes through as a populated list of ints
        assert all(isinstance(aid, int) for aid in first.advertiser_ids)


class TestCreativeDetail:
    def test_get_creative_unwraps_envelope(self):
        """Single-creative responses are wrapped in ``{"creative": {...}}``."""
        client = _replay({"/services/v4/creative_resources/2427707": FIXTURES / "single.json"})
        creative = client.get_creative(2427707)

        assert creative.id == 2427707
        assert creative.base_ad_unit == "video"
        # No renditions inline without ?include=renditions
        assert creative.renditions == []

    def test_get_creative_with_renditions(self):
        client = _replay({"/services/v4/creative_resources/2427707": FIXTURES / "single_with_renditions.json"})
        creative = client.get_creative(2427707, include_renditions=True)

        # Query param goes onto the URL
        call = client._transport._session.request.call_args.kwargs
        assert "include=renditions" in call["url"]

        # Renditions populated from the response
        assert len(creative.renditions) >= 1
        first_rendition = creative.renditions[0]
        assert first_rendition.id is not None
        # Anonymised fixture: VAST URI replaced but field still present
        assert first_rendition.uri is not None
        assert first_rendition.uri.startswith("https://")
