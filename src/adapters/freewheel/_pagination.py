"""Shared pagination helpers for FreeWheel clients.

The v3 and v4 surfaces use compatible pagination envelopes
(:class:`PaginatedResponse` exposes ``items`` and ``total_page``), so the
walk-every-page logic is identical for both. Lives here so the two clients
don't drift.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def iter_pages(list_fn: Any, *, per_page: int) -> Iterator[Any]:
    """Yield every item across every page returned by ``list_fn``.

    ``list_fn`` must accept ``page`` + ``per_page`` keyword arguments and
    return an object with ``.items`` and ``.total_page`` attributes (any
    :class:`PaginatedResponse`).
    """
    page = 1
    while True:
        envelope = list_fn(page=page, per_page=per_page)
        yield from envelope.items
        if page >= envelope.total_page:
            return
        page += 1
