"""Canonical valid AdCP wire bodies for ``_delegate_*`` tests.

The ``_delegate_*`` functions in ``core.platforms._delegate`` validate the
incoming request through Pydantic before dispatching to the impl. Tests that
exercise *post-validation* behavior (translation, forwarding, response
projection) need a body that passes coercion — the specific field values
don't matter, but the shape must satisfy ``CreateMediaBuyRequest`` and
friends.

Centralizing these bodies prevents the same minimal-valid shape from
diverging across tests when the request schema changes (e.g. adding a new
required field). Each helper returns a fresh ``dict`` so tests can mutate
without affecting each other.
"""

from __future__ import annotations

from typing import Any


def minimal_create_media_buy_body() -> dict[str, Any]:
    """Smallest body that passes ``CreateMediaBuyRequest`` validation.

    Used by tests that exercise the create_media_buy delegate path without
    caring about the impl's actual semantics (e.g. translation tests,
    forwarding tests). Mutate the returned dict freely.

    ``account`` and ``idempotency_key`` are spec-required on adcp 5.0+; the
    spec-correct shape includes them.
    """
    return {
        "account": {"account_id": "test-acct"},
        "idempotency_key": "idem-test-xxxxxxxxxxxxxxxx",
        "brand": {"domain": "testbrand.com"},
        "packages": [
            {
                "product_id": "prod_1",
                "budget": 1000.0,
                "pricing_option_id": "po-cpm-default",
            }
        ],
        "start_time": "2026-06-01T00:00:00Z",
        "end_time": "2026-06-30T00:00:00Z",
    }
