"""Tests for the FreeWheel adapter's static creative format declaration."""

from __future__ import annotations

from adcp.types import Format

from src.adapters.freewheel.formats import freewheel_creative_formats


class TestFreeWheelCreativeFormats:
    def test_returns_six_canonical_formats(self):
        formats = freewheel_creative_formats(tenant_id="t1")
        assert len(formats) == 6
        ids = sorted(f["format_id"]["id"] for f in formats)
        assert ids == sorted(
            [
                "freewheel_video_15s_pre_roll",
                "freewheel_video_30s_pre_roll",
                "freewheel_video_15s_mid_roll",
                "freewheel_video_30s_mid_roll",
                "freewheel_video_15s_post_roll",
                "freewheel_video_30s_post_roll",
            ]
        )

    def test_agent_url_carries_tenant_scoping(self):
        formats = freewheel_creative_formats(tenant_id="talpa")
        for fmt in formats:
            assert fmt["format_id"]["agent_url"] == "freewheel://talpa"

    def test_agent_url_falls_back_when_tenant_is_none(self):
        formats = freewheel_creative_formats(tenant_id=None)
        for fmt in formats:
            assert fmt["format_id"]["agent_url"] == "freewheel://default"

    def test_each_format_validates_against_adcp_format_schema(self):
        """Every declared format must parse cleanly as an adcp.types.Format."""
        for fmt in freewheel_creative_formats(tenant_id="t1"):
            Format.model_validate(fmt)  # raises if invalid

    def test_format_carries_vast_tag_asset(self):
        for fmt in freewheel_creative_formats(tenant_id="t1"):
            assert fmt["type"] == "video"
            assert len(fmt["assets"]) == 1
            asset = fmt["assets"][0]
            assert asset["asset_id"] == "vast_tag_url"
            assert asset["required"] is True


class TestAdapterIntegration:
    def test_adapter_get_creative_formats_returns_static_list(self):
        from unittest.mock import MagicMock

        from src.adapters.freewheel import FreeWheelAdapter

        principal = MagicMock()
        principal.principal_id = "p1"
        principal.get_adapter_id.return_value = "1356511"
        principal.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}

        adapter = FreeWheelAdapter(
            config={"api_token": "test-token"},
            principal=principal,
            dry_run=True,
            tenant_id="talpa",
        )

        formats = adapter.get_creative_formats()
        assert len(formats) == 6
        assert all(f["format_id"]["agent_url"] == "freewheel://talpa" for f in formats)
