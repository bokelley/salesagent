"""Static creative format declarations for the FreeWheel adapter.

FreeWheel delivers video through VAST tag forwarding: the publisher (Talpa)
hosts ad slots; buyers provide VAST tag URLs via creative_resources; the
ad server resolves them at delivery time. The format space is roughly the
cartesian product of:

  * **slot position** — pre-roll, mid-roll, post-roll
  * **duration bucket** — 15s, 30s (the two most common; FreeWheel itself
    classifies content into short/mid/long form which constrains but does
    not strictly define ad duration)
  * **rendition shape** — width × height × content_type — captured per
    asset rather than per format, so it doesn't multiply the declared set.

This module exposes a static list of the canonical six combinations as
AdCP Format dicts. Adapter callers get them via
``FreeWheelAdapter.get_creative_formats()``.

We declare these statically (Option A) rather than synthesising from
``content_durations × ad_units`` synced data because (a) AdCP's format
registry is mostly static today, (b) the six canonical formats cover the
common case, and (c) static declaration keeps the format_ids stable
across inventory-sync runs.
"""

from __future__ import annotations

from typing import Any

from src.adapters._format_helpers import vast_format


def freewheel_creative_formats(tenant_id: str | None) -> list[dict[str, Any]]:
    """Return the FreeWheel adapter's supported creative formats.

    ``tenant_id`` scopes the synthesised ``agent_url`` so format ownership
    is traceable back to the specific tenant. When unset, defaults to
    ``default`` (matches the Broadstreet adapter's behaviour).
    """
    agent_url = f"freewheel://{tenant_id or 'default'}"

    specs: list[tuple[str, str, str]] = [
        ("freewheel_video_15s_pre_roll", "Video 15s Pre-Roll", "15-second VAST video before content playback."),
        ("freewheel_video_30s_pre_roll", "Video 30s Pre-Roll", "30-second VAST video before content playback."),
        ("freewheel_video_15s_mid_roll", "Video 15s Mid-Roll", "15-second VAST video during content playback."),
        ("freewheel_video_30s_mid_roll", "Video 30s Mid-Roll", "30-second VAST video during content playback."),
        ("freewheel_video_15s_post_roll", "Video 15s Post-Roll", "15-second VAST video after content playback."),
        ("freewheel_video_30s_post_roll", "Video 30s Post-Roll", "30-second VAST video after content playback."),
    ]
    return [vast_format(fid, name, desc, agent_url, media_type="video") for fid, name, desc in specs]
