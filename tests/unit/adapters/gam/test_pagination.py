from types import SimpleNamespace

import pytest

from src.adapters.gam.pagination import IncompleteGAMPage, iter_gam_statement_results


class _StatementBuilder:
    def __init__(self):
        self.offset = 0

    def ToStatement(self):
        return {"offset": self.offset}


def _page(ids: list[int], total: int) -> dict:
    return {"results": ids, "totalResultSetSize": total}


def test_iter_gam_statement_results_allows_legitimate_zero_total():
    builder = _StatementBuilder()

    results = list(iter_gam_statement_results(lambda _stmt: _page([], 0), builder, label="things"))

    assert results == []
    assert builder.offset == 0


def test_iter_gam_statement_results_paginates_until_total_exhausted():
    builder = _StatementBuilder()
    pages = [_page([1, 2], 3), _page([3], 3)]

    def fetch(_stmt):
        return pages.pop(0)

    results = list(iter_gam_statement_results(fetch, builder, label="things"))

    assert results == [1, 2, 3]
    assert builder.offset == 2


def test_iter_gam_statement_results_rejects_mid_stream_empty_page():
    builder = _StatementBuilder()
    pages = [_page([1], 2), _page([], 2)]

    def fetch(_stmt):
        return pages.pop(0)

    with pytest.raises(IncompleteGAMPage, match="1/2"):
        list(iter_gam_statement_results(fetch, builder, label="things"))


def test_iter_gam_statement_results_rejects_missing_page():
    builder = _StatementBuilder()

    with pytest.raises(IncompleteGAMPage, match="no things page"):
        list(iter_gam_statement_results(lambda _stmt: None, builder, label="things"))


def test_iter_gam_statement_results_rejects_empty_first_page_without_total():
    builder = _StatementBuilder()

    with pytest.raises(IncompleteGAMPage, match="without totalResultSetSize"):
        list(iter_gam_statement_results(lambda _stmt: {"results": []}, builder, label="things"))


def test_iter_gam_statement_results_accepts_soap_objects():
    builder = _StatementBuilder()
    pages = [
        SimpleNamespace(results=[SimpleNamespace(id=1)], totalResultSetSize=2),
        SimpleNamespace(results=[SimpleNamespace(id=2)], totalResultSetSize=2),
    ]

    def fetch(_stmt):
        return pages.pop(0)

    results = list(iter_gam_statement_results(fetch, builder, label="things"))

    assert [row.id for row in results] == [1, 2]
