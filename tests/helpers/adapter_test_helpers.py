"""Shared test helpers for adapter unit tests.

Adapter-level tests across Triton, FreeWheel, etc. all need to invoke the
``create_media_buy`` method with the same boilerplate (request + packages +
start/end times). Extract that into a single helper so individual test files
can focus on the assertions that differ.
"""

from __future__ import annotations

from typing import Any


def invoke_create_media_buy(
    adapter: Any,
    request: Any,
    packages: list[Any],
    package_pricing_info: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Call ``adapter.create_media_buy()`` with the request's start/end times.

    Used by ``test_triton_adapter.py``, ``test_freewheel_adapter.py``, and any
    future adapter tests that share the same invocation shape.

    If ``package_pricing_info`` is omitted, a default fixed-CPM entry is
    synthesized for every package, matching what ``media_buy_create`` produces
    in production.
    """
    if package_pricing_info is None:
        package_pricing_info = {
            pkg.package_id: {
                "pricing_model": "cpm",
                "rate": 10.0,
                "currency": "USD",
                "is_fixed": True,
                "bid_price": None,
            }
            for pkg in packages
        }
    return adapter.create_media_buy(
        request=request,
        packages=packages,
        start_time=request.start_time,
        end_time=request.end_time,
        package_pricing_info=package_pricing_info,
    )
