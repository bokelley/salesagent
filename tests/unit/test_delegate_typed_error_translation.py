"""Cross-transport contract test: every typed ``AdCPError`` subclass raised by
an ``_impl`` projects to the same framework :class:`AdcpError` envelope on
BOTH the MCP and A2A delegate paths.

Issue #319: ``measurement_terms_rejected/create_media_buy_aggressive_terms``,
``measurement_terms_rejected/create_media_buy_relaxed_terms``, and
``invalid_transitions/update_unknown_media_buy`` storyboard scenarios passed
on MCP but failed on A2A because the A2A delegate path applied a different
(or absent) translator. This test pins the invariant — the **same delegate**
serves both transports, so a typed error MUST translate identically on both.

Architecture pinned here:

* The DRY translator lives on every ``_delegate_*`` function via the
  :func:`core.platforms._delegate.translate_adcp_errors` decorator.
* Both ``MockSellerPlatform`` and ``GamPlatform`` invoke the same delegate,
  so the wire envelope is by-construction identical across transports.
* New ``AdCPError`` subclasses inherit translation by adding the decorator
  to their delegate; no per-error wiring required.

The test asserts on the framework ``AdcpError`` exception that the delegate
re-raises — that's the seam the framework dispatcher catches and projects
onto the wire ``adcp_error`` envelope (MCP: ``structuredContent.adcp_error``;
A2A: ``Task.artifacts[0].parts[0].data.adcp_error``).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from adcp.decisioning import AdcpError as WireAdcpError

from src.core.exceptions import (
    AdCPMediaBuyNotFoundError,
    AdCPPackageNotFoundError,
    AdCPTermsRejectedError,
)
from tests.unit.helpers.delegate_request_bodies import minimal_create_media_buy_body

# ---------------------------------------------------------------------------
# Test matrix: (delegate name, raised AdCPError subclass, expected wire code)
# ---------------------------------------------------------------------------
#
# The matrix exercises one delegate per AdCPError subclass. Within each case
# both ``mcp`` and ``a2a`` are run via parametrization — the delegate doesn't
# branch on transport, so a regression that drops translation on either path
# fails the same assertion in both rows.
ERROR_CASES = [
    pytest.param(
        "_delegate_update_media_buy",
        AdCPMediaBuyNotFoundError,
        "MEDIA_BUY_NOT_FOUND",
        "Media buy 'mb_x' not found.",
        id="update_media_buy-MEDIA_BUY_NOT_FOUND",
    ),
    pytest.param(
        "_delegate_update_media_buy",
        AdCPPackageNotFoundError,
        "PACKAGE_NOT_FOUND",
        "Package 'pkg_x' not found",
        id="update_media_buy-PACKAGE_NOT_FOUND",
    ),
    pytest.param(
        "_delegate_create_media_buy",
        AdCPTermsRejectedError,
        "TERMS_REJECTED",
        "max_variance_percent below seller minimum",
        id="create_media_buy-TERMS_REJECTED",
    ),
]


def _identity_stub() -> Any:
    """Minimal stand-in for ``ResolvedIdentity`` — the translation path
    doesn't read off it, so any object with a stable identity suffices."""
    return type("FakeIdent", (), {"protocol": "mcp"})()


def _invoke_delegate(delegate_name: str, side_effect: Exception) -> Any:
    """Patch the impl that the named delegate forwards to and call the
    delegate. Each delegate has different positional signatures and patches
    a different impl module — this helper centralizes that mapping so the
    test body stays focused on the translation invariant.
    """
    from core.platforms import _delegate

    fake_ctx = object()

    if delegate_name == "_delegate_update_media_buy":
        with patch.object(_delegate, "_update_media_buy_impl", side_effect=side_effect):
            with patch.object(_delegate, "_build_identity", return_value=_identity_stub()):
                return asyncio.run(_delegate._delegate_update_media_buy("mb_x", {}, fake_ctx))

    if delegate_name == "_delegate_create_media_buy":
        # ``_create_media_buy_impl`` is async; raise via a coroutine factory
        # so ``await`` inside the delegate sees the exception.
        async def _raise(*args: Any, **kwargs: Any) -> Any:
            raise side_effect

        # Translation runs after request coercion, so the body must validate
        # cleanly to reach the impl. Helper returns a fresh dict; mutate freely.
        req = minimal_create_media_buy_body()
        with patch.object(_delegate, "_create_media_buy_impl", side_effect=_raise):
            with patch.object(_delegate, "_build_identity", return_value=_identity_stub()):
                return asyncio.run(_delegate._delegate_create_media_buy(req, fake_ctx))

    raise AssertionError(f"unmapped delegate {delegate_name!r}")


# ---------------------------------------------------------------------------
# Cross-transport invariant: same delegate serves both — wire shape is identical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", ["mcp", "a2a"])
@pytest.mark.parametrize("delegate_name,error_class,expected_code,error_message", ERROR_CASES)
def test_typed_error_translates_to_wire_envelope_on_both_transports(
    transport: str,
    delegate_name: str,
    error_class: type,
    expected_code: str,
    error_message: str,
) -> None:
    """The delegate MUST raise the framework :class:`AdcpError` carrying the
    spec error code, regardless of transport.

    Both ``MockSellerPlatform`` and ``GamPlatform`` route through the same
    ``_delegate_*`` function — there is exactly one translator path. The
    transport parameter is here so a regression that special-cases either
    path (e.g. by branching on ``identity.protocol``) fails one row of the
    matrix loudly, instead of silently passing on the path the developer
    happened to test.
    """
    raised = error_class(error_message)
    # Stamp ``current_transport`` so ``_build_identity`` records the correct
    # ``ResolvedIdentity.protocol``. Translation MUST not depend on this — but
    # if a future refactor adds a transport-conditional translator, the
    # parametrized run flips one row and surfaces the regression.
    with patch("core.platforms._delegate.current_transport", MagicMock(get=MagicMock(return_value=transport))):
        with pytest.raises(WireAdcpError) as exc_info:
            _invoke_delegate(delegate_name, raised)

    assert exc_info.value.code == expected_code, (
        f"transport={transport} delegate={delegate_name} class={error_class.__name__}: "
        f"expected wire code {expected_code!r}, got {exc_info.value.code!r}"
    )
    # Recovery hint comes from the salesagent enum and must round-trip through
    # ``_RECOVERY_HINT_MAP`` to the framework's enum unchanged. All three
    # AdCPError subclasses in the matrix declare ``recovery='correctable'``,
    # which is the spec recovery for buyer-fixable rejections.
    assert exc_info.value.recovery == "correctable", (
        f"transport={transport} delegate={delegate_name}: "
        f"recovery hint dropped or remapped to {exc_info.value.recovery!r}"
    )
    # Message must be preserved verbatim — buyer agents key retry decisions
    # off the human-readable message in addition to the code.
    assert error_message in (
        exc_info.value.args[0] if exc_info.value.args else ""
    ), f"transport={transport} delegate={delegate_name}: message {error_message!r} did not survive translation"


# ---------------------------------------------------------------------------
# Decorator-shape invariant: every delegate is wrapped, so the matrix can grow
# without per-error wiring
# ---------------------------------------------------------------------------


def test_every_delegate_is_decorated_with_translate_adcp_errors() -> None:
    """Architectural guard: any new ``_delegate_*`` function added to
    ``core.platforms._delegate`` MUST be wrapped with
    :func:`translate_adcp_errors`. Without the decorator, typed AdCPError
    rejections raised inside that delegate's ``_impl`` would surface as
    ``INTERNAL_ERROR`` on both transports — same regression class as #319.

    The decorator is detected via ``functools.wraps``: the wrapped function
    closes over the original via ``__wrapped__``.
    """
    from core.platforms import _delegate

    delegates = [
        name for name in dir(_delegate) if name.startswith("_delegate_") and callable(getattr(_delegate, name))
    ]
    assert delegates, "no _delegate_* functions found — refactor changed the convention"

    undecorated = []
    for name in delegates:
        fn = getattr(_delegate, name)
        # ``functools.wraps`` sets ``__wrapped__`` on the wrapper; bare async
        # delegates have no such attribute. The wrapper itself is the public
        # symbol, so we check the existence of ``__wrapped__``.
        if not hasattr(fn, "__wrapped__"):
            undecorated.append(name)

    assert not undecorated, (
        f"delegates missing @translate_adcp_errors decorator: {undecorated}. "
        f"Add the decorator from core.platforms._delegate so typed AdCPError "
        f"subclasses translate to the wire envelope on both MCP and A2A."
    )
