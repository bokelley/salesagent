"""Spec-required kwargs for AdCP request models.

The library declares ``account`` and ``idempotency_key`` as required on
``CreateMediaBuyRequest``, ``UpdateMediaBuyRequest``, and
``SyncCreativesRequest``. Tests that don't care about the specific values
use these helpers to satisfy validation:

    from tests.factories.spec_required_kwargs import required_request_kwargs

    req = CreateMediaBuyRequest(
        **required_request_kwargs(),
        brand={"domain": "example.com"},
        packages=[...],
        ...
    )

Tests that DO care (e.g. exercising idempotency-key replay) pass the
field explicitly — kwargs in the call override the helper defaults.
"""

from __future__ import annotations

import uuid
from typing import Any

from adcp.types import AccountReference


def required_request_kwargs(*, account_id: str = "test-acct", **overrides: Any) -> dict[str, Any]:
    """Return ``account`` + ``idempotency_key`` defaults satisfying the
    AdCP spec's required-field validation. Spread as ``**kwargs`` into
    ``CreateMediaBuyRequest``, ``UpdateMediaBuyRequest``, or
    ``SyncCreativesRequest``.

    The ``idempotency_key`` is freshly minted per call, matching the
    spec contract that buyers generate a fresh UUID per request.

    Pass ``**overrides`` (e.g. ``idempotency_key="key-abcxxxxxxxxx"``) to control
    a specific value while still satisfying required-field validation.
    Useful when a test asserts on the dedup key. Overrides win over
    defaults — never produces ``got multiple values for kwarg``.
    """
    base: dict[str, Any] = {
        "account": AccountReference(account_id=account_id),
        "idempotency_key": f"idem-test-{uuid.uuid4().hex}",
    }
    base.update(overrides)
    return base
