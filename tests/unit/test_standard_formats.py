"""Unit tests for the hardcoded standard-formats catalog.

Verifies:
- All 12 IAB standard formats parse as valid :class:`Format` objects.
- ``get_standard_format`` returns the cached object for known IDs and
  ``None`` for unknown IDs.
- ``is_standard_agent`` matches the reference creative agent URL with
  trailing-slash + scheme tolerance.
- ``CreativeAgentRegistry.get_format`` short-circuits to the catalog
  without a network call when the agent_url is the reference agent.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from adcp import Format

from src.core.standard_formats import (
    STANDARD_AGENT_URL,
    STANDARD_FORMAT_IDS,
    STANDARD_FORMATS,
    get_standard_format,
    is_standard_agent,
)


class TestStandardFormatCatalog:
    def test_catalog_has_iab_standards(self):
        """The 12 IAB standard formats from format_cache.py are present."""
        expected = {
            "display_300x250",
            "display_728x90",
            "display_160x600",
            "display_300x600",
            "display_320x50",
            "display_970x250",
            "video_640x480",
            "video_1280x720",
            "video_1920x1080",
            "audio_30s",
            "audio_60s",
            "native_1x1",
        }
        assert STANDARD_FORMAT_IDS == expected

    def test_every_entry_is_a_real_format_object(self):
        """All catalog entries deserialize as Format objects (proves the
        nested asset definitions match the v4.4.0 Format schema)."""
        for fmt_id, fmt in STANDARD_FORMATS.items():
            assert isinstance(fmt, Format), f"{fmt_id} is not a Format"
            assert fmt.format_id.id == fmt_id
            # Pydantic AnyUrl normalizes by adding trailing slash; strip
            # before comparing to avoid the cosmetic mismatch.
            assert str(fmt.format_id.agent_url).rstrip("/") == STANDARD_AGENT_URL

    def test_get_known_format_returns_object(self):
        fmt = get_standard_format("display_300x250")
        assert fmt is not None
        assert fmt.format_id.id == "display_300x250"
        assert fmt.type == "display"

    def test_get_unknown_format_returns_none(self):
        assert get_standard_format("display_1x1_unknown_size") is None


class TestIsStandardAgent:
    def test_exact_match(self):
        assert is_standard_agent(STANDARD_AGENT_URL) is True

    def test_trailing_slash_tolerated(self):
        assert is_standard_agent(STANDARD_AGENT_URL + "/") is True

    def test_other_url_rejected(self):
        assert is_standard_agent("https://example.com") is False
        assert is_standard_agent("https://creative.scope3.com") is False

    def test_empty_rejected(self):
        assert is_standard_agent("") is False
        assert is_standard_agent(None) is False  # type: ignore[arg-type]


class TestRegistryShortCircuit:
    def test_get_format_short_circuits_for_standard_agent(self):
        """Standard agent + standard format → returns from catalog
        without calling get_formats_for_agent (the network round trip)."""
        from src.core.creative_agent_registry import CreativeAgentRegistry

        registry = CreativeAgentRegistry()

        with patch.object(
            registry, "get_formats_for_agent", new=AsyncMock(return_value=[])
        ) as mock_network:
            fmt = asyncio.run(registry.get_format(STANDARD_AGENT_URL, "display_300x250"))
            assert fmt is not None
            assert fmt.format_id.id == "display_300x250"
            # Network path was NOT touched.
            mock_network.assert_not_called()

    def test_get_format_falls_through_for_custom_agent(self):
        """A non-standard agent_url skips the catalog and hits the
        live registry lookup. Custom-format tenants are unaffected."""
        from src.core.creative_agent_registry import CreativeAgentRegistry

        registry = CreativeAgentRegistry()

        with patch.object(
            registry, "get_formats_for_agent", new=AsyncMock(return_value=[])
        ) as mock_network:
            fmt = asyncio.run(
                registry.get_format("https://creative.example.com", "display_300x250")
            )
            assert fmt is None  # mocked to empty
            mock_network.assert_called_once()

    def test_get_format_falls_through_for_unknown_format_on_standard_agent(self):
        """Standard agent but unknown format ID still hits the network —
        custom formats published by the reference agent are still discoverable."""
        from src.core.creative_agent_registry import CreativeAgentRegistry

        registry = CreativeAgentRegistry()

        with patch.object(
            registry, "get_formats_for_agent", new=AsyncMock(return_value=[])
        ) as mock_network:
            asyncio.run(registry.get_format(STANDARD_AGENT_URL, "exotic_unknown_format"))
            mock_network.assert_called_once()
