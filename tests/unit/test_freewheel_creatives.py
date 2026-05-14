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


class TestCreativeWriteSurface:
    """POST/DELETE wiring verified against the live FW shapes captured in
    PR #381 — both creative_resources (envelope-wrapped on write, doubly-
    wrapped on response under ``data``) and creative_instances (flat both
    directions)."""

    def _client_with_response(self, status_code: int, body: dict | str):
        """Build a client whose underlying session returns a single canned
        response. Lets each test pin the exact request shape we send."""
        import json as _json
        from unittest.mock import MagicMock

        body_text = _json.dumps(body) if isinstance(body, dict) else body
        mock_session = MagicMock()
        mock_session.request.return_value = MagicMock(
            status_code=status_code,
            ok=200 <= status_code < 300,
            content=body_text.encode(),
            text=body_text,
            json=lambda: _json.loads(body_text),
        )
        return FreeWheelCreativeClient(FreeWheelTransport(api_token="t", session=mock_session))

    def test_create_creative_wraps_request_under_creative_key(self):
        """FW returns 400 'Creative Node is missing' if the body isn't wrapped
        under ``{"creative": {...}}``. Verified live 2026-05-13."""
        from src.adapters.freewheel.entities import Creative

        # FW's actual create-response shape: {"data": {"success": ..., "creative": {...}}}
        response = {
            "data": {
                "success": True,
                "creative": {
                    "id": 335926557,
                    "name": "smoke",
                    "advertiser_ids": [679485],
                    "base_ad_unit_id": 1,
                    "base_ad_unit": "video",
                    "status": "PROCESSING",
                    "external_id": "adcp-smoke-001",
                },
            }
        }
        client = self._client_with_response(200, response)
        created = client.create_creative(
            name="smoke", advertiser_ids=[679485], base_ad_unit_id=1, external_id="adcp-smoke-001"
        )

        assert isinstance(created, Creative)
        assert created.id == 335926557
        assert created.name == "smoke"

        # Verify wire body was wrapped under "creative" — FW rejects the flat shape
        call_kwargs = client._transport._session.request.call_args.kwargs
        import json as _json

        sent = _json.loads(call_kwargs["data"])
        assert "creative" in sent, "FW expects request body wrapped under 'creative' key"
        assert sent["creative"]["name"] == "smoke"
        assert sent["creative"]["external_id"] == "adcp-smoke-001"

    def test_create_creative_instance_sends_ad_id_as_ad_unit_node(self):
        """FW's body param is called ``ad_id`` but is actually an
        ad_unit_node_id (per their docs). The response auto-populates
        ``placement_id``. Verified live 2026-05-13."""
        response = {
            "id": 57369958,
            "ad_id": 90997227,
            "creative_id": 335926557,
            "placement_id": 90997225,  # auto-populated by FW
            "status": "ACTIVE",
        }
        client = self._client_with_response(201, response)
        result = client.create_creative_instance(ad_unit_node_id=90997227, creative_id=335926557)

        assert result["id"] == 57369958
        assert result["placement_id"] == 90997225

        # Verify the wire body uses ``ad_id`` (FW's param name), not ad_unit_node_id
        call_kwargs = client._transport._session.request.call_args.kwargs
        import json as _json

        sent = _json.loads(call_kwargs["data"])
        assert sent == {"ad_id": 90997227, "creative_id": 335926557}

    def test_delete_creative_calls_correct_path(self):
        client = self._client_with_response(200, "")
        client.delete_creative(335926557)
        call_kwargs = client._transport._session.request.call_args.kwargs
        assert call_kwargs["method"] == "DELETE"
        assert call_kwargs["url"].endswith("/services/v4/creative_resources/335926557")

    def test_delete_creative_instance_calls_correct_path(self):
        client = self._client_with_response(200, "")
        client.delete_creative_instance(57369958)
        call_kwargs = client._transport._session.request.call_args.kwargs
        assert call_kwargs["method"] == "DELETE"
        assert call_kwargs["url"].endswith("/services/v4/creative_instances/57369958")
