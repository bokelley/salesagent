"""Tests for the ``_ReplayMarkingStore`` wrap override.

Regression coverage for salesagent #342 finding 1: cached-response replays
must carry the AdCP envelope-level ``replayed: true`` flag (L1/security
idempotency rule 4). The upstream :class:`IdempotencyStore.wrap` returns the
cached response verbatim — the library docstring on
:class:`CachedResponse` explicitly states that "the seller injects
``replayed: true`` at the envelope level before sending", so the salesagent
subclass performs that injection on the cache-hit branch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from adcp.exceptions import IdempotencyConflictError

import core.idempotency as idem


@pytest.fixture(autouse=True)
def _force_memory_backend(monkeypatch):
    """Pin the test to MemoryBackend so we don't need a Postgres pool."""
    monkeypatch.setenv("CORE_IDEMPOTENCY_BACKEND", "memory")
    idem.reset_for_tests()
    yield
    idem.reset_for_tests()


def _ctx_with_principal(principal_id: str = "buyer_1") -> MagicMock:
    """Build a context object exposing ``caller_identity`` for scope-key
    extraction — matches the framework's ToolContext duck-type."""
    ctx = MagicMock()
    ctx.caller_identity = principal_id
    ctx.tenant_id = "tenant_a"
    return ctx


class TestReplayMarkingStoreInjectsReplayedFlag:
    """The subclass wrap injects ``replayed: true`` on cache-hit envelopes."""

    @pytest.mark.asyncio
    async def test_first_call_carries_no_replayed_flag(self):
        """The fresh-handler path returns the original response verbatim —
        no ``replayed`` flag is added on a cache miss."""
        store = idem.get_idempotency_store()
        # The salesagent's lazy singleton may have constructed a non-subclass
        # in a prior test process; reset_for_tests + the autouse fixture
        # guarantee a fresh store here.
        assert isinstance(store, idem._ReplayMarkingStore)

        calls = {"n": 0}

        @store.wrap
        async def handler(self: Any, params: dict[str, Any], ctx: Any) -> dict[str, Any]:
            calls["n"] += 1
            return {"media_buy_id": "mb_123", "status": "active"}

        params = {"idempotency_key": "key-aaaaaaaaaaaaaaaa-0001", "brand": {"domain": "example.com"}}
        result = await handler(None, params, _ctx_with_principal())

        assert calls["n"] == 1
        assert result == {"media_buy_id": "mb_123", "status": "active"}
        assert "replayed" not in result

    @pytest.mark.asyncio
    async def test_replay_call_injects_replayed_true(self):
        """A second call with the same key + same payload returns the cached
        response with ``replayed: true`` injected at the top level."""
        store = idem.get_idempotency_store()
        assert isinstance(store, idem._ReplayMarkingStore)

        calls = {"n": 0}

        @store.wrap
        async def handler(self: Any, params: dict[str, Any], ctx: Any) -> dict[str, Any]:
            calls["n"] += 1
            return {"media_buy_id": "mb_123", "status": "active"}

        params = {"idempotency_key": "key-bbbbbbbbbbbbbbbb-0002", "brand": {"domain": "example.com"}}
        ctx = _ctx_with_principal()

        first = await handler(None, params, ctx)
        second = await handler(None, params, ctx)

        # Handler ran exactly once — the second call replayed from cache.
        assert calls["n"] == 1
        # The first call has no ``replayed`` field.
        assert "replayed" not in first
        # The replay carries ``replayed: true`` per AdCP L1/security rule 4.
        assert second.get("replayed") is True
        # Domain payload is otherwise byte-stable.
        assert second["media_buy_id"] == first["media_buy_id"]
        assert second["status"] == first["status"]

    @pytest.mark.asyncio
    async def test_replay_does_not_mutate_cached_response(self):
        """Multiple replays each get their own ``replayed=True`` dict — the
        cached response is not mutated, so future replays still see the same
        original payload."""
        store = idem.get_idempotency_store()

        @store.wrap
        async def handler(self: Any, params: dict[str, Any], ctx: Any) -> dict[str, Any]:
            return {"media_buy_id": "mb_xyz", "packages": [{"package_id": "p1"}]}

        params = {"idempotency_key": "key-cccccccccccccccc-0003"}
        ctx = _ctx_with_principal()
        await handler(None, params, ctx)  # warm cache

        replay_a = await handler(None, params, ctx)
        replay_a["replayed"] = False  # caller mutates their copy
        replay_a["packages"][0]["package_id"] = "mutated"

        replay_b = await handler(None, params, ctx)
        # Caller mutation must not leak into the next replay.
        assert replay_b["replayed"] is True
        assert replay_b["packages"][0]["package_id"] == "p1"

    @pytest.mark.asyncio
    async def test_conflict_path_still_raises(self):
        """Reusing the key with a different payload hash still raises
        :class:`IdempotencyConflictError` — our cache-hit shortcut only fires
        on hash match, so the library's conflict-raise path is preserved."""
        store = idem.get_idempotency_store()

        @store.wrap
        async def handler(self: Any, params: dict[str, Any], ctx: Any) -> dict[str, Any]:
            return {"ok": True}

        ctx = _ctx_with_principal()
        await handler(None, {"idempotency_key": "key-dddddddddddddddd-0004", "amount": 100}, ctx)

        with pytest.raises(IdempotencyConflictError):
            await handler(None, {"idempotency_key": "key-dddddddddddddddd-0004", "amount": 200}, ctx)
