"""IdempotencyConflictError → IDEMPOTENCY_CONFLICT wire-mapping translator.

The framework's :class:`adcp.server.idempotency.IdempotencyStore.wrap` raises
:class:`adcp.exceptions.IdempotencyConflictError` when the same idempotency_key
is reused with a materially different payload. Without translation that
exception bubbles past the dispatcher's structured-error catch and surfaces on
the wire as ``INTERNAL_ERROR`` (terminal recovery) — discarding the spec's
distinction between replay-conflict (correctable) and server failure
(terminal). Issue #178.

These tests pin the wire-projection contract for the
``translate_idempotency_conflict`` decorator (``core/idempotency.py``):
the decorator catches the framework conflict exception and re-raises a
wire-shaped ``AdcpError`` with ``code="IDEMPOTENCY_CONFLICT"`` and
``recovery="correctable"`` so buyers can either resend the original payload
or rotate the key and retry.

End-to-end coverage of the dispatcher → wire envelope projection (verifying
that buyers actually see ``code="IDEMPOTENCY_CONFLICT"`` on the wire, not
just that the decorator translates the exception) is tracked in #295 — the
platform now delegates to DB-backed ``_impl`` functions, so a proper
end-to-end test needs the harness/factory pattern rather than the in-memory
``_MEDIA_BUYS`` store the original test relied on.
"""

from __future__ import annotations

from typing import Any

import pytest
from adcp.decisioning import AdcpError
from adcp.exceptions import IdempotencyConflictError

from core.idempotency import translate_idempotency_conflict


class TestTranslateIdempotencyConflictDecorator:
    """The decorator catches the framework's conflict exception and re-raises
    a wire-shaped :class:`AdcpError` with the spec-mandated code + recovery.
    """

    @pytest.mark.asyncio
    async def test_idempotency_conflict_translates_to_adcp_error(self):
        """A framework :class:`IdempotencyConflictError` becomes an
        :class:`AdcpError` with ``code="IDEMPOTENCY_CONFLICT"`` and
        ``recovery="correctable"``.
        """

        @translate_idempotency_conflict
        async def handler() -> dict[str, Any]:
            raise IdempotencyConflictError(
                operation="create_media_buy",
                errors=[
                    {
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "idempotency_key reused with a different payload",
                    }
                ],
            )

        with pytest.raises(AdcpError) as exc_info:
            await handler()

        assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
        assert exc_info.value.recovery == "correctable"
        # __cause__ preserved so server logs link the wire error to the
        # framework's underlying exception for debugging.
        assert isinstance(exc_info.value.__cause__, IdempotencyConflictError)

    @pytest.mark.asyncio
    async def test_non_conflict_exceptions_pass_through(self):
        """The decorator must not swallow unrelated exceptions — only
        :class:`IdempotencyConflictError` is translated.
        """

        @translate_idempotency_conflict
        async def handler() -> dict[str, Any]:
            raise ValueError("unrelated failure")

        with pytest.raises(ValueError, match="unrelated failure"):
            await handler()

    @pytest.mark.asyncio
    async def test_success_path_returns_handler_result(self):
        """The decorator must not interfere with the success path."""

        @translate_idempotency_conflict
        async def handler() -> dict[str, Any]:
            return {"ok": True}

        assert await handler() == {"ok": True}

    @pytest.mark.asyncio
    async def test_existing_adcp_error_pass_through(self):
        """An :class:`AdcpError` raised by the inner handler (e.g.
        :class:`AdcpError("INVALID_REQUEST")`) must reach the dispatcher
        unchanged. The decorator only catches IdempotencyConflictError.
        """

        @translate_idempotency_conflict
        async def handler() -> dict[str, Any]:
            raise AdcpError("INVALID_REQUEST", message="bad input", recovery="correctable")

        with pytest.raises(AdcpError) as exc_info:
            await handler()

        assert exc_info.value.code == "INVALID_REQUEST"
        assert exc_info.value.recovery == "correctable"
