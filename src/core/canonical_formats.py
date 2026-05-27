"""Shared AdCP reference-agent canonical creative format IDs."""

from __future__ import annotations

from typing import Any

DEFAULT_CREATIVE_AGENT_URL = "https://creative.adcontextprotocol.org"
REFERENCE_CREATIVE_AGENT_URL_ALIASES = frozenset(
    {
        DEFAULT_CREATIVE_AGENT_URL,
        "https://adcontextprotocol.org/agents/formats",
    }
)


def normalize_reference_agent_url(agent_url: Any) -> str:
    """Return the canonical reference-agent URL when ``agent_url`` is a known alias."""
    normalized = str(agent_url).rstrip("/")
    if normalized.endswith("/mcp"):
        normalized = normalized.removesuffix("/mcp").rstrip("/")
    if normalized in REFERENCE_CREATIVE_AGENT_URL_ALIASES:
        return DEFAULT_CREATIVE_AGENT_URL
    return normalized


def is_reference_creative_agent_url(agent_url: Any) -> bool:
    """Return whether ``agent_url`` identifies the AdCP reference format catalog."""
    if not agent_url:
        return False
    return normalize_reference_agent_url(agent_url) == DEFAULT_CREATIVE_AGENT_URL


CANONICAL_DISPLAY_FORMAT_IDS = ("display_image", "display_html", "display_js")
CANONICAL_CAROUSEL_FORMAT_IDS = (
    "product_carousel_display",
    "image_slideshow_5s_each",
    "mobile_story_vertical",
    "video_playlist_6s_bumpers",
)
CANONICAL_VIDEO_FORMAT_IDS = ("video_standard", "video_vast")
CANONICAL_AUDIO_FORMAT_IDS = ("audio_vast", "audio_15s", "audio_30s", "audio_60s")
CANONICAL_NATIVE_FORMAT_IDS = ("native_standard",)

CANONICAL_FORMAT_IDS = frozenset(
    CANONICAL_DISPLAY_FORMAT_IDS
    + CANONICAL_CAROUSEL_FORMAT_IDS
    + CANONICAL_VIDEO_FORMAT_IDS
    + CANONICAL_AUDIO_FORMAT_IDS
    + CANONICAL_NATIVE_FORMAT_IDS
)

DISPLAY_FORMAT_LABELS = {
    "display_image": "image",
    "display_html": "HTML5",
    "display_js": "JS",
}


def canonical_format_ref(format_id: str, **params: Any) -> dict[str, Any]:
    """Return a structured FormatId reference for the standard creative agent."""
    ref: dict[str, Any] = {
        "agent_url": DEFAULT_CREATIVE_AGENT_URL,
        "id": format_id,
    }
    ref.update({key: value for key, value in params.items() if value is not None})
    return ref


__all__ = [
    "CANONICAL_AUDIO_FORMAT_IDS",
    "CANONICAL_CAROUSEL_FORMAT_IDS",
    "CANONICAL_DISPLAY_FORMAT_IDS",
    "CANONICAL_FORMAT_IDS",
    "CANONICAL_NATIVE_FORMAT_IDS",
    "CANONICAL_VIDEO_FORMAT_IDS",
    "DEFAULT_CREATIVE_AGENT_URL",
    "DISPLAY_FORMAT_LABELS",
    "REFERENCE_CREATIVE_AGENT_URL_ALIASES",
    "canonical_format_ref",
    "is_reference_creative_agent_url",
    "normalize_reference_agent_url",
]
