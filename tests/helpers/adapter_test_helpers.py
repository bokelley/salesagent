"""Shared test helpers for adapter unit tests.

Adapter-level tests across Triton, FreeWheel, SpringServe, etc. all need to
invoke the ``create_media_buy`` method with the same boilerplate (request +
packages + start/end times). Extract that into a single helper so individual
test files can focus on the assertions that differ.

Also exports the ``sample_request`` / ``sample_packages`` factory helpers
used as pytest fixtures by every adapter's unit-test module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage


def make_sample_create_request() -> CreateMediaBuyRequest:
    """Build a representative AdCP ``CreateMediaBuyRequest`` for adapter tests.

    Single source of truth -- every adapter's ``test_*_adapter.py`` calls this
    via a thin fixture so the shape stays consistent and the duplicate-code
    guard is satisfied.
    """
    from tests.factories.spec_required_kwargs import required_request_kwargs
    from tests.helpers.adcp_factories import create_test_package_request

    start = datetime.now(UTC)
    return CreateMediaBuyRequest(
        **required_request_kwargs(),
        brand={"domain": "brand.example.com"},
        packages=[create_test_package_request(product_id="prod_video_1")],
        start_time=start,
        end_time=start + timedelta(days=14),
    )


def make_sample_video_package(package_id: str = "pkg_video_1") -> MediaPackage:
    """Build a representative video ``MediaPackage`` for adapter tests."""
    return MediaPackage(
        package_id=package_id,
        name="Pre-roll Bundle",
        delivery_type="guaranteed",
        impressions=500_000,
        format_ids=[FormatId(agent_url="https://test.com", id="video_15s")],
    )


def stub_http_response(status_code: int, *, content: bytes = b"", text: str = "") -> Any:
    """Build a MagicMock that quacks like a ``requests.Response``.

    Used by every adapter's transport unit tests to fake ``session.request``
    return values. Centralised here so ``test_*_transport.py`` modules don't
    re-declare the same helper and trip the duplicate-code guard.
    """
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.status_code = status_code
    mock.ok = 200 <= status_code < 400
    mock.content = content
    mock.text = text
    mock.json.return_value = {} if not content else None
    return mock


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
