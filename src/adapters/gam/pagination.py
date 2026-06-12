"""Google Ad Manager statement pagination helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


class IncompleteGAMPage(RuntimeError):
    """GAM returned an empty page before ``totalResultSetSize`` was exhausted."""


def _page_results(page: Any) -> list[Any]:
    if isinstance(page, dict):
        return list(page.get("results") or [])
    return list(getattr(page, "results", []) or [])


def _total_result_set_size(page: Any) -> int | None:
    if page is None:
        return None
    if isinstance(page, dict):
        total = page.get("totalResultSetSize")
    else:
        total = getattr(page, "totalResultSetSize", None)
    return int(total) if total is not None else None


def iter_gam_statement_results(
    fetch_page: Callable[[Any], Any],
    statement_builder: Any,
    *,
    label: str,
) -> Iterable[Any]:
    """Yield paged GAM statement results with a mid-stream empty-page guard."""
    total: int | None = None
    fetched = 0
    while True:
        page = fetch_page(statement_builder.ToStatement())
        if page is None:
            raise IncompleteGAMPage(f"GAM returned no {label} page")
        if total is None:
            total = _total_result_set_size(page)
        results = _page_results(page)
        if not results:
            if fetched == 0 and total is None:
                raise IncompleteGAMPage(f"GAM returned empty {label} page without totalResultSetSize")
            if total is not None and fetched < total:
                raise IncompleteGAMPage(
                    f"GAM returned empty {label} page before totalResultSetSize was exhausted ({fetched}/{total})"
                )
            break

        yield from results
        fetched += len(results)
        if total is not None and fetched >= total:
            break
        statement_builder.offset += len(results)
