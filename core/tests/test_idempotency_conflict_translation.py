"""Unit tests for IdempotencyConflictError → IDEMPOTENCY_CONFLICT translation.

The framework's :class:`IdempotencyStore.wrap` raises
:class:`adcp.exceptions.IdempotencyConflictError` when the same
idempotency_key is reused with a materially different payload. Without
translation that exception bubbles past the dispatcher's structured-error
catch and surfaces on the wire as ``INTERNAL_ERROR`` (terminal recovery) —
discarding the spec's distinction between replay-conflict (correctable)
and server failure (terminal).

These tests pin the wire-projection contract:

- ``translate_idempotency_conflict`` decorator catches the framework
  conflict exception and re-raises a wire-shaped ``AdcpError`` with
  ``code="IDEMPOTENCY_CONFLICT"`` and ``recovery="correctable"``.
- End-to-end through the mock platform: same idempotency_key + different
  payload raises the translated framework ``AdcpError`` so the dispatcher
  projects it onto the AdCP error envelope verbatim.

Closes the IdempotencyConflictError half of the storyboard's
``measurement_terms_rejected/create_media_buy_relaxed_terms`` failure
(parallel to PR #133's TERMS_REJECTED fix).
"""

from __future__ import annotations

from typing import Any

import pytest
from adcp.decisioning import AdcpError
from adcp.exceptions import IdempotencyConflictError
from adcp.testing import make_request_context

import core.idempotency as idem
from core.idempotency import translate_idempotency_conflict


@pytest.fixture(autouse=True)
def _memory_backend(monkeypatch):
    """Force the idempotency store to MemoryBackend so the conflict can be
    triggered without a Postgres dependency."""
    monkeypatch.setenv("CORE_IDEMPOTENCY_BACKEND", "memory")
    idem.reset_for_tests()
    yield
    idem.reset_for_tests()


# ---------------------------------------------------------------------------
# Decorator unit tests — translate_idempotency_conflict in isolation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# End-to-end through MockSellerPlatform.create_media_buy
# ---------------------------------------------------------------------------


def _make_ctx():
    """Build a RequestContext shaped like the framework dispatcher's calls
    into a platform method. The wrap reads ``caller_identity`` to scope the
    cache (one buyer's reused key cannot collide with another's), and
    ``account.metadata.tenant_id`` for the salesagent tenant lookup.
    """
    return make_request_context(
        account="test_tenant:test_account",
        tenant_id="test_tenant",
        auth_principal="prin_test",
        caller_identity="prin_test",  # required for IdempotencyStore.wrap dedup
        metadata={"tenant_id": "test_tenant"},
    )


class TestPlatformCreateMediaBuyConflict:
    """End-to-end: invoking the mock platform's :meth:`create_media_buy`
    twice with the same idempotency_key and different payloads must surface
    the IDEMPOTENCY_CONFLICT wire code, not INTERNAL_ERROR.

    The mock platform's body is irrelevant here — the conflict is raised
    inside ``@_IDEMPOTENCY.wrap`` BEFORE the body runs on the second call.
    The translator decorator stacked OUTSIDE the wrap catches it and
    re-raises as :class:`AdcpError`.
    """

    @pytest.mark.asyncio
    async def test_conflict_on_repeat_with_different_payload(self):
        """Two calls with the same ``idempotency_key`` and different payloads
        raise :class:`AdcpError` ``IDEMPOTENCY_CONFLICT`` (correctable).
        """
        # Import inside the test so the autouse fixture's env flip + store
        # reset have already taken effect when the platform module's
        # module-level ``_IDEMPOTENCY = get_idempotency_store()`` runs.
        from core.platforms.mock import _MEDIA_BUYS, MockSellerPlatform

        _MEDIA_BUYS.clear()
        platform = MockSellerPlatform()

        idempotency_key = "test-key-abc-123"
        # Use plain dicts — the mock platform's _get_packages handles
        # both Pydantic models and dicts, so we keep this test free of
        # Pydantic schema construction noise.
        first_payload = {
            "idempotency_key": idempotency_key,
            "packages": [{"product_ids": ["prod_1"], "budget": 5000.0}],
        }
        second_payload = {
            "idempotency_key": idempotency_key,
            "packages": [{"product_ids": ["prod_1"], "budget": 9999.0}],  # different
        }

        ctx = _make_ctx()

        # First call seeds the cache. The mock platform body runs; whatever
        # it returns is what we'll get on a true replay (same payload).
        first_result = await platform.create_media_buy(first_payload, ctx)
        assert "media_buy_id" in first_result

        # Second call with same key, different payload → conflict.
        with pytest.raises(AdcpError) as exc_info:
            await platform.create_media_buy(second_payload, ctx)

        assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
        assert exc_info.value.recovery == "correctable"

    @pytest.mark.asyncio
    async def test_replay_with_same_payload_returns_cached(self):
        """Two calls with the same ``idempotency_key`` AND same payload return
        the cached response — no conflict. This guards against the translator
        over-rejecting valid replays.
        """
        from core.platforms.mock import _MEDIA_BUYS, MockSellerPlatform

        _MEDIA_BUYS.clear()
        platform = MockSellerPlatform()

        idempotency_key = "test-key-xyz-789"
        payload = {
            "idempotency_key": idempotency_key,
            "packages": [{"product_ids": ["prod_1"], "budget": 5000.0}],
        }

        ctx = _make_ctx()

        first = await platform.create_media_buy(payload, ctx)
        second = await platform.create_media_buy(payload, ctx)

        # Same idempotency_key + same payload → cached replay, same media_buy_id.
        assert first["media_buy_id"] == second["media_buy_id"]
