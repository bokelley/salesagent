"""Hardcoded catalog of AdCP standard formats.

Resolves to a :class:`Format` object without a network call to the
reference creative agent (https://creative.adcontextprotocol.org).
Tenants that only use standard formats — the common case — never need
to register a custom creative agent for their own deployment, and
dev/CI environments without internet keep working.

The catalog covers the IAB standards that GAM (and most ad servers)
support out of the box: display, video, audio, native. Any format ID
NOT in this catalog falls through to the live registry lookup, so
custom-format tenants are unaffected.

Wired via :func:`CreativeAgentRegistry.get_format` — the registry checks
this catalog first when ``agent_url`` matches the reference creative
agent and the ``format_id`` is in :data:`STANDARD_FORMAT_IDS`. See
``src/core/creative_agent_registry.py``.
"""

from __future__ import annotations

from typing import Any

from adcp import Format
from adcp.types import FormatId as LibraryFormatId

# Reference creative agent — every salesagent deployment defaults to it
# regardless of tenant config. See CreativeAgentRegistry.DEFAULT_AGENT.
STANDARD_AGENT_URL = "https://creative.adcontextprotocol.org"


def _format_id(fmt_id: str) -> LibraryFormatId:
    return LibraryFormatId(agent_url=STANDARD_AGENT_URL, id=fmt_id)


def _display_format(format_id: str, name: str, width: int, height: int) -> Format:
    """IAB display banner — single image asset, fixed dimensions."""
    return Format(
        format_id=_format_id(format_id),
        name=name,
        type="display",
        assets=[
            {
                "item_type": "individual",
                "asset_id": "image",
                "asset_type": "image",
                "required": True,
                "width": width,
                "height": height,
            },
        ],
    )


def _video_format(format_id: str, name: str, width: int, height: int) -> Format:
    """IAB video — single video asset with dimensions."""
    return Format(
        format_id=_format_id(format_id),
        name=name,
        type="video",
        assets=[
            {
                "item_type": "individual",
                "asset_id": "video",
                "asset_type": "video",
                "required": True,
                "width": width,
                "height": height,
            },
        ],
    )


def _audio_format(format_id: str, name: str, duration_s: int) -> Format:
    """IAB audio — single audio asset with duration constraint."""
    return Format(
        format_id=_format_id(format_id),
        name=name,
        type="audio",
        assets=[
            {
                "item_type": "individual",
                "asset_id": "audio",
                "asset_type": "audio",
                "required": True,
                "duration_ms": duration_s * 1000,
            },
        ],
    )


def _native_format(format_id: str, name: str) -> Format:
    """Native — flexible bundle of title + image + body."""
    return Format(
        format_id=_format_id(format_id),
        name=name,
        type="native",
        assets=[
            {"item_type": "individual", "asset_id": "title", "asset_type": "text", "required": True},
            {"item_type": "individual", "asset_id": "image", "asset_type": "image", "required": True},
            {"item_type": "individual", "asset_id": "body", "asset_type": "text", "required": False},
        ],
    )


# The catalog. Format IDs match the format_cache.py legacy mapping —
# anything in this dict short-circuits the network round trip.
STANDARD_FORMATS: dict[str, Format] = {
    # --- Display (IAB Standard Ad Sizes) ---
    "display_300x250": _display_format("display_300x250", "Medium Rectangle", 300, 250),
    "display_728x90": _display_format("display_728x90", "Leaderboard", 728, 90),
    "display_160x600": _display_format("display_160x600", "Wide Skyscraper", 160, 600),
    "display_300x600": _display_format("display_300x600", "Half Page", 300, 600),
    "display_320x50": _display_format("display_320x50", "Mobile Banner", 320, 50),
    "display_970x250": _display_format("display_970x250", "Billboard", 970, 250),
    # --- Video ---
    "video_640x480": _video_format("video_640x480", "Video SD 4:3", 640, 480),
    "video_1280x720": _video_format("video_1280x720", "Video HD 720p", 1280, 720),
    "video_1920x1080": _video_format("video_1920x1080", "Video HD 1080p", 1920, 1080),
    # --- Audio ---
    "audio_30s": _audio_format("audio_30s", "Audio 30s", 30),
    "audio_60s": _audio_format("audio_60s", "Audio 60s", 60),
    # --- Native ---
    "native_1x1": _native_format("native_1x1", "Native 1:1"),
}


# Lookup-friendly set for fast membership checks (the registry uses this
# before constructing the Format object).
STANDARD_FORMAT_IDS: frozenset[str] = frozenset(STANDARD_FORMATS.keys())


def get_standard_format(format_id: str) -> Format | None:
    """Return the hardcoded :class:`Format` for ``format_id``, or None.

    Caller compares ``agent_url`` to :data:`STANDARD_AGENT_URL` first —
    only standard-agent format requests should hit this catalog.
    """
    return STANDARD_FORMATS.get(format_id)


def is_standard_agent(agent_url: str) -> bool:
    """``True`` if ``agent_url`` matches the reference creative agent.

    Tolerant of trailing slashes and protocol normalization (the YARL
    canonicalization the registry uses on cache keys is overkill for this
    boolean check).
    """
    if not agent_url:
        return False
    return agent_url.rstrip("/") == STANDARD_AGENT_URL.rstrip("/")


__all__: list[Any] = [
    "STANDARD_AGENT_URL",
    "STANDARD_FORMAT_IDS",
    "STANDARD_FORMATS",
    "get_standard_format",
    "is_standard_agent",
]
