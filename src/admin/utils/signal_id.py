"""Auto-generate AdCP-compliant signal_ids from operator-supplied names.

AdCP wire constraint (``Signal.signal_id.id``): ``^[a-zA-Z0-9_-]+$``. The
authoring UX collects a human-readable ``name``; this utility slugifies
it to a stable id the operator never types.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_SLUG_KILL = re.compile(r"[^a-zA-Z0-9_-]+")
_COLLAPSE = re.compile(r"_+")

# Reserve characters that AdCP wire validation rejects. The slug never
# exceeds 200 chars — TenantSignal.signal_id is String(200).
_MAX_LEN = 180  # leave headroom for the disambiguation suffix


def slugify_signal_id(name: str) -> str:
    """Convert a free-form name to an AdCP signal_id candidate.

    Lowercases, replaces unsafe characters with ``_``, collapses runs of
    underscores. Returns an empty string only for inputs that contain no
    [a-zA-Z0-9_-] characters at all — callers must check + reject.
    """
    if not name:
        return ""
    slug = _SLUG_KILL.sub("_", name.strip().lower())
    slug = _COLLAPSE.sub("_", slug).strip("_-")
    return slug[:_MAX_LEN]


def unique_signal_id(name: str, exists: Callable[[str], bool]) -> str:
    """Slugify ``name`` and disambiguate against ``exists(candidate)``.

    On collision, appends ``_2``, ``_3``, … until the candidate is free.
    Falls back to ``"signal"`` if the slug is empty after sanitization
    (e.g. the operator's name was entirely non-ASCII punctuation).
    """
    base = slugify_signal_id(name) or "signal"
    if not exists(base):
        return base
    counter = 2
    while exists(f"{base}_{counter}"):
        counter += 1
    return f"{base}_{counter}"
