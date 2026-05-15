"""Shared helpers for declaring static VAST creative formats.

Adapters that deliver video or audio through VAST tag forwarding (FreeWheel,
SpringServe, and any future SSAI-based adapter) declare a small static set
of Format dicts -- pre/mid/post-roll x duration. They all share the same
asset shape (one ``vast_tag_url`` asset) and the same JSON envelope; extract
that shape here so each adapter just supplies its identifier slugs.
"""

from __future__ import annotations

from typing import Any

# Asset spec common to every VAST format declared via this module. The
# rendition dimensions and MIME types are carried at the Creative layer;
# the format itself just declares the slot for a VAST tag URL.
_VAST_TAG_ASSET: dict[str, Any] = {
    "item_type": "individual",
    "asset_id": "vast_tag_url",
    "asset_type": "url",
    "required": True,
    "name": "VAST Tag URL",
}


def vast_format(
    format_id: str,
    name: str,
    description: str,
    agent_url: str,
    media_type: str = "video",
) -> dict[str, Any]:
    """Build one AdCP Format dict for a VAST-delivered slot.

    ``media_type`` is "video" or "audio". The ``delivery`` envelope flags
    the format as VAST-delivered so callers downstream can route creative
    rendering correctly.
    """
    return {
        "format_id": {"id": format_id, "agent_url": agent_url},
        "name": name,
        "type": media_type,
        "description": description,
        "assets": [_VAST_TAG_ASSET],
        "delivery": {"vast": True},
    }
