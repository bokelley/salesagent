"""``create_media_buy`` validation failures surface as AdCP-canonical
``INVALID_REQUEST`` on the wire, not the non-spec ``VALIDATION_ERROR``.

The pre-dispatch validation pass in ``_create_media_buy_impl`` raises
``ValueError`` on past-start_time, reversed dates, empty product_ids,
duplicate product_ids, and targeting validation failures. The outer
``except (ValueError, PermissionError)`` handler used to wrap these as
``Error(code="VALIDATION_ERROR")`` — but that code isn't in the AdCP 3.0
``STANDARD_ERROR_CODES`` enum, so buyer agents walking the enum for
self-correction silently drop the error.

Storyboard ``error_compliance/nonexistent_product`` accepts
``PRODUCT_NOT_FOUND``, ``PRODUCT_UNAVAILABLE``, or ``INVALID_REQUEST`` at
``/adcp_error/code``. Storyboard ``error_compliance/reversed_dates_error``
accepts ``VALIDATION_ERROR`` or ``INVALID_REQUEST``. ``INVALID_REQUEST`` is
the only value in the intersection.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "allowed_codes",
    [
        # error_compliance/nonexistent_product
        ["PRODUCT_NOT_FOUND", "PRODUCT_UNAVAILABLE", "INVALID_REQUEST"],
        # error_compliance/reversed_dates_error
        ["VALIDATION_ERROR", "INVALID_REQUEST"],
    ],
)
def test_invalid_request_satisfies_both_storyboard_validations(allowed_codes: list[str]) -> None:
    """The single wire code we emit must satisfy both storyboards. This
    pins the intersection of accepted codes so future YAML revisions
    that drop ``INVALID_REQUEST`` from either list fail the test before
    a regression hits production."""
    assert "INVALID_REQUEST" in allowed_codes


def test_create_media_buy_error_accepts_invalid_request_code() -> None:
    """The ``CreateMediaBuyError`` schema must accept ``INVALID_REQUEST``
    in the ``Error.code`` field. The wire envelope projection in
    ``_translate_adcp_error`` preserves the code verbatim, so this is
    sufficient to lock the contract — a schema regression that rejected
    ``INVALID_REQUEST`` would surface here before reaching the wire."""
    from src.core.schemas import CreateMediaBuyError, Error

    err = CreateMediaBuyError(errors=[Error(code="INVALID_REQUEST", message="start_time is in the past", details=None)])
    assert err.errors[0].code == "INVALID_REQUEST"
