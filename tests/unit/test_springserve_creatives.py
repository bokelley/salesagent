"""Tests for SpringServeCreativesClient -- typed CRUD over /videos.

The endpoint hosts BOTH video and audio creatives. Discrimination is via
``creative_format`` ("video"|"audio") + ``creative_content_type``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._creatives import SpringServeCreativesClient
from src.adapters.springserve.entities import VideoCreative


@pytest.fixture
def transport():
    return MagicMock()


@pytest.fixture
def client(transport):
    return SpringServeCreativesClient(transport)


def _video_response(video_id: int = 1182735, **overrides) -> dict:
    body = {
        "id": video_id,
        "account_id": 1730,
        "demand_partner_id": 88061,
        "name": "adcp_video",
        "creative_format": "video",
        "creative_content_type": "video/mp4",
        "creative_remote_url": "https://cdn.example.com/spot.mp4",
        "type": "VideoCreative",
        "active": True,
        "duration_seconds": 15,
        "width": 1920,
        "height": 1080,
        "line_item_demand_tag_ids": [],
    }
    body.update(overrides)
    return body


class TestCreateVideo:
    def test_minimal_video_creative(self, client, transport):
        transport.post_json.return_value = _video_response()
        result = client.create(
            name="spot_15s",
            demand_partner_id=88061,
            creative_remote_url="https://cdn.example.com/spot.mp4",
        )

        transport.post_json.assert_called_once_with(
            "/videos",
            {
                "name": "spot_15s",
                "demand_partner_id": 88061,
                "creative_format": "video",
                "creative_content_type": "video/mp4",
                "creative_remote_url": "https://cdn.example.com/spot.mp4",
                "active": True,
            },
        )
        assert isinstance(result, VideoCreative)
        assert result.id == 1182735
        assert result.creative_format == "video"

    def test_optional_fields_included_when_set(self, client, transport):
        transport.post_json.return_value = _video_response()
        client.create(
            name="x",
            demand_partner_id=1,
            creative_remote_url="https://x",
            duration_seconds=30,
            width=1280,
            height=720,
            creative_landing_page_url="https://landing.example.com",
            secondary_code="adcp_creative_42",
        )
        body = transport.post_json.call_args.args[1]
        assert body["duration_seconds"] == 30
        assert body["width"] == 1280
        assert body["height"] == 720
        assert body["creative_landing_page_url"] == "https://landing.example.com"
        assert body["secondary_code"] == "adcp_creative_42"


class TestCreateAudio:
    """Audio creatives share the /videos endpoint -- discrimination via
    creative_format + creative_content_type."""

    def test_audio_mpeg(self, client, transport):
        transport.post_json.return_value = _video_response(creative_format="audio", creative_content_type="audio/mpeg")
        client.create(
            name="audio_spot_30s",
            demand_partner_id=88061,
            creative_remote_url="https://cdn.example.com/spot.mp3",
            creative_format="audio",
            creative_content_type="audio/mpeg",
        )
        body = transport.post_json.call_args.args[1]
        assert body["creative_format"] == "audio"
        assert body["creative_content_type"] == "audio/mpeg"

    def test_audio_mp4(self, client, transport):
        transport.post_json.return_value = _video_response(creative_format="audio", creative_content_type="audio/mp4")
        client.create(
            name="audio_spot",
            demand_partner_id=88061,
            creative_remote_url="https://cdn.example.com/spot.m4a",
            creative_format="audio",
            creative_content_type="audio/mp4",
        )
        body = transport.post_json.call_args.args[1]
        assert body["creative_format"] == "audio"
        assert body["creative_content_type"] == "audio/mp4"


class TestGet:
    def test_returns_typed_creative(self, client, transport):
        transport.get_json.return_value = _video_response(1182999)
        result = client.get(1182999)
        transport.get_json.assert_called_once_with("/videos/1182999")
        assert result.id == 1182999


class TestUpdate:
    def test_active_toggle(self, client, transport):
        transport.put_json.return_value = _video_response(active=False)
        client.update(1182735, active=False)
        transport.put_json.assert_called_once_with("/videos/1182735", {"active": False})


class TestDelete:
    def test_delete_calls_delete_json(self, client, transport):
        client.delete(1182735)
        transport.delete_json.assert_called_once_with("/videos/1182735")


class TestVideoCreativeEntity:
    def test_extra_fields_round_trip(self):
        body = _video_response()
        body["tag_pixels"] = [{"id": 1, "pixel_type": "AdClickThru", "pixel_url": "https://t.example.com"}]
        creative = VideoCreative.model_validate(body)
        dumped = creative.model_dump()
        assert dumped["tag_pixels"] == [{"id": 1, "pixel_type": "AdClickThru", "pixel_url": "https://t.example.com"}]

    def test_audio_creative_validates(self):
        body = _video_response(creative_format="audio", creative_content_type="audio/mpeg")
        creative = VideoCreative.model_validate(body)
        assert creative.creative_format == "audio"
