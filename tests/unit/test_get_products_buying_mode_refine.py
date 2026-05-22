"""Regression: ``buying_mode='refine'`` must not be combined with ``brief``.

Locks in the AdCP spec rule through the seller's production validator.

## Spec evidence

1. ``adcp.types.generated_poc.media_buy.get_products_request.GetProductsRequest``
   field ``brief`` (line 168-173 of the generated module): "Required when
   buying_mode is 'brief'. Must not be provided when buying_mode is 'wholesale'
   or 'refine'."
2. ``src.core.tools.products.validate_get_products_buying_mode`` delegates to
   ``adcp.decisioning.refine.assert_buying_mode_consistent`` (the SDK's
   pre-dispatch validator wired into ``adcp.decisioning.handler``) and
   translates SDK ``AdcpError`` into local ``AdCPValidationError``.
3. ``buying_mode`` field description: "'refine': iterate on products and
   proposals from a previous get_products response using the refine array of
   change requests." — i.e. the ``refine[]`` array drives iteration, not a
   brief.

## Why this test exists

Storyboard ``media_buy_seller/refine_products`` step ``get_products_refine``
appears to fail against our seller with::

    INVALID_REQUEST: buying_mode='refine' must not be combined with brief.

Investigation (#105) showed the seller is correct per spec, and the storyboard
step's own ``sample_request`` does NOT include ``brief`` — the failure is
upstream (storyboard runner injecting brief from a prior step or the
storyboard fixture being malformed). This test pins the seller's
spec-conformant behavior so we can't accidentally relax it to "fix" the
storyboard noise.

If the AdCP spec ever changes to PERMIT ``brief + refine``, the SDK's
validator will be updated upstream and this test will fail through our
production boundary — that's the right place to discover the spec change.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AdCPInvalidRequestError
from src.core.schemas import GetProductsRequest
from src.core.tools.products import validate_get_products_buying_mode


def _build_request(**kwargs):
    """Helper: construct a production ``GetProductsRequest`` with explicit fields."""
    return GetProductsRequest(**kwargs)


def test_refine_with_brief_rejected_by_production_validator():
    """``buying_mode='refine'`` + ``brief`` must raise ``INVALID_REQUEST`` per spec.

    Spec: ``GetProductsRequest.brief`` description — "Must not be provided
    when buying_mode is ... 'refine'."
    Validator: ``adcp.decisioning.refine.assert_buying_mode_consistent``.
    """
    req = _build_request(
        buying_mode="refine",
        brief="find me video ads",
        refine=[{"scope": "request", "ask": "more video options"}],
    )

    with pytest.raises(AdCPInvalidRequestError) as exc_info:
        validate_get_products_buying_mode(req)

    err = exc_info.value
    assert err.error_code == "INVALID_REQUEST"
    assert err.details == {"sdk_error_code": "INVALID_REQUEST", "field": "brief"}
    # Lock in the human-readable message — Wonderstruck emits this verbatim
    # from the SDK and the storyboard reporter quotes it. Tests that include
    # this string make the spec rationale unmistakable.
    assert "must not be combined with brief" in str(err)
    assert "refine[] array drives iteration" in str(err)


def test_refine_without_brief_passes_sdk_validator():
    """``buying_mode='refine'`` with only ``refine[]`` (no brief) is valid.

    This matches the storyboard ``refine_products/get_products_refine``
    step's actual ``sample_request`` shape (see
    ``npx @adcp/sdk storyboard show media_buy_seller/refine_products``).
    """
    req = _build_request(
        buying_mode="refine",
        refine=[{"scope": "request", "ask": "Only guaranteed packages"}],
    )

    # Must not raise. Validator returns None on success.
    assert validate_get_products_buying_mode(req) is None


def test_refine_without_refine_array_rejected():
    """``buying_mode='refine'`` requires a non-empty ``refine[]`` array.

    Spec/validator: ``assert_buying_mode_consistent`` raises
    ``INVALID_REQUEST`` with ``field='refine'`` when the buyer asks for
    refine mode but provides no change requests.
    """
    req = _build_request(buying_mode="refine")

    with pytest.raises(AdCPInvalidRequestError) as exc_info:
        validate_get_products_buying_mode(req)

    err = exc_info.value
    assert err.error_code == "INVALID_REQUEST"
    assert err.details == {"sdk_error_code": "INVALID_REQUEST", "field": "refine"}


def test_wholesale_with_brief_rejected():
    """``buying_mode='wholesale'`` + ``brief`` is also a spec violation.

    Included as a sibling case from the same validator — guards against
    future drift on the wholesale arm of the same mutual-exclusion rule.
    """
    req = _build_request(buying_mode="wholesale", brief="anything")

    with pytest.raises(AdCPInvalidRequestError) as exc_info:
        validate_get_products_buying_mode(req)

    assert exc_info.value.details == {"sdk_error_code": "INVALID_REQUEST", "field": "brief"}


def test_brief_mode_with_brief_passes():
    """``buying_mode='brief'`` + ``brief`` is the canonical happy path."""
    req = _build_request(
        buying_mode="brief",
        brief="Premium video, Q2 flight, $50K, US adults 25-54",
    )

    assert validate_get_products_buying_mode(req) is None
