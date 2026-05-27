"""Adapter logging helpers."""

from __future__ import annotations


def safe_upstream_body_excerpt(body: str | None, *, limit: int = 500) -> str:
    """Short log-only excerpt that avoids common token/secret leakage shapes."""
    if not body:
        return ""
    excerpt = body.replace("\n", " ").replace("\r", " ")[:limit]
    for marker in ("access_token", "api_token", "password", "Authorization"):
        if marker.lower() in excerpt.lower():
            return "<redacted: upstream body contained secret-like field>"
    return excerpt
