"""Static creative format declarations for the SpringServe adapter.

SpringServe delivers video and audio through VAST tag forwarding or
hosted creatives (POST /api/v0/videos with a remote URL or multipart MP4
upload, max 500 MB). The format space is roughly:

  * **media type** -- video, audio
  * **slot position** -- pre-roll, mid-roll, post-roll (post-roll uncommon
    in audio; verify with Talpa during Stage 3)
  * **duration bucket** -- 15s, 30s
  * **rendition shape** -- width x height x content_type -- captured per
    asset rather than per format

We declare these statically (Option A) rather than synthesising from
inventory data so format_ids stay stable across inventory sync runs.

Audio support is a first-class concern, not a sidecar: SpringServe's
Magnite x iHeartMedia marketplace runs audio (streaming + podcast) on
the same demand-tag API surface as video, just with audio MIME types on
the creative records (audio/mp4, audio/mpeg).
"""

from __future__ import annotations

from typing import Any

from src.adapters._format_helpers import vast_format


def springserve_creative_formats(tenant_id: str | None) -> list[dict[str, Any]]:
    """Return the SpringServe adapter's supported creative formats.

    ``tenant_id`` scopes the synthesised ``agent_url`` so format ownership
    is traceable back to the specific tenant.
    """
    agent_url = f"springserve://{tenant_id or 'default'}"

    video_specs: list[tuple[str, str, str]] = [
        ("springserve_video_15s_pre_roll", "Video 15s Pre-Roll", "15-second VAST video before content playback."),
        ("springserve_video_30s_pre_roll", "Video 30s Pre-Roll", "30-second VAST video before content playback."),
        ("springserve_video_15s_mid_roll", "Video 15s Mid-Roll", "15-second VAST video during content playback."),
        ("springserve_video_30s_mid_roll", "Video 30s Mid-Roll", "30-second VAST video during content playback."),
        ("springserve_video_15s_post_roll", "Video 15s Post-Roll", "15-second VAST video after content playback."),
        ("springserve_video_30s_post_roll", "Video 30s Post-Roll", "30-second VAST video after content playback."),
    ]
    audio_specs: list[tuple[str, str, str]] = [
        ("springserve_audio_15s_pre_roll", "Audio 15s Pre-Roll", "15-second VAST audio before content playback."),
        ("springserve_audio_30s_pre_roll", "Audio 30s Pre-Roll", "30-second VAST audio before content playback."),
        ("springserve_audio_15s_mid_roll", "Audio 15s Mid-Roll", "15-second VAST audio during content playback."),
        ("springserve_audio_30s_mid_roll", "Audio 30s Mid-Roll", "30-second VAST audio during content playback."),
    ]
    return [vast_format(fid, name, desc, agent_url, media_type="video") for fid, name, desc in video_specs] + [
        vast_format(fid, name, desc, agent_url, media_type="audio") for fid, name, desc in audio_specs
    ]
