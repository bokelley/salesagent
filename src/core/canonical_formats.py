"""Shared AdCP reference-agent canonical creative format IDs."""

from __future__ import annotations

from typing import Any

DEFAULT_CREATIVE_AGENT_URL = "https://creative.adcontextprotocol.org"


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


def normalize_creative_agent_url(agent_url: Any) -> str:
    """Normalize an agent URL for creative-format identity comparisons."""
    if not agent_url:
        return ""

    normalized = str(agent_url).rstrip("/")
    if normalized.endswith("/mcp"):
        normalized = normalized.removesuffix("/mcp").rstrip("/")

    if normalized == DEFAULT_CREATIVE_AGENT_URL:
        return DEFAULT_CREATIVE_AGENT_URL
    return normalized


def normalize_reference_agent_url(agent_url: Any) -> str:
    """Backward-compatible name for creative-agent URL normalization."""
    return normalize_creative_agent_url(agent_url)


def is_reference_creative_agent_url(agent_url: Any) -> bool:
    """Return true only for the AdCP reference creative agent URL."""
    return normalize_creative_agent_url(agent_url) == DEFAULT_CREATIVE_AGENT_URL


__all__ = [
    "CANONICAL_AUDIO_FORMAT_IDS",
    "CANONICAL_CAROUSEL_FORMAT_IDS",
    "CANONICAL_DISPLAY_FORMAT_IDS",
    "CANONICAL_FORMAT_IDS",
    "CANONICAL_NATIVE_FORMAT_IDS",
    "CANONICAL_VIDEO_FORMAT_IDS",
    "DEFAULT_CREATIVE_AGENT_URL",
    "DISPLAY_FORMAT_LABELS",
    "canonical_format_ref",
    "is_reference_creative_agent_url",
    "normalize_creative_agent_url",
    "normalize_reference_agent_url",
]
