"""Shared environment parsing helpers."""

from __future__ import annotations

import os


def env_bool(name: str, *, default: bool) -> bool:
    """Parse an environment boolean with an explicit default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
