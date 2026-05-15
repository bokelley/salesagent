"""Bootstrap + access for the process-wide :class:`PgProposalStore`.

Replaces the prior :class:`SalesAgentProposalStore` (PR #390) after the
upstream library shipped its own durable implementation in adcp 5.5.0
(adcontextprotocol/adcp-client-python#732). The upstream store handles
the CAS, the cross-tenant rejection, the TTL bookkeeping, and the
``ON CONFLICT`` upsert semantics — all the parts our local store had to
reimplement.

Pattern mirrors :mod:`core.idempotency`:

* Lazy process-singleton :class:`AsyncConnectionPool` (separate from the
  idempotency pool so the two surfaces have independent lifecycles).
* Pool opens on first async call (not at construction time) so
  ``AsyncConnectionPool``'s worker tasks bind to the same event loop
  that ``serve()`` ends up driving.
* Schema is managed by Alembic, not by :meth:`PgProposalStore.create_schema`
  — we add a salesagent-internal ``tenant_id`` generated column + FK
  to ``tenants`` that the upstream's stock ``create_schema`` omits.
  Calling ``create_schema`` after the migration would be a no-op
  (``CREATE TABLE IF NOT EXISTS``), but the migration is the source of
  truth.

Recipe decoder is the upstream default: ``Recipe.model_validate``.
:class:`SalesAgentProposalManager` stores the base :class:`Recipe`
shape (no typed subclasses like GAMRecipe), so the default works.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from adcp.decisioning.pg.proposal_store import PgProposalStore as _UpstreamPgProposalStore

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class _LazyOpenPgProposalStore(_UpstreamPgProposalStore):
    """Subclass that opens its pool on the first async method call.

    Why: ``serve()``'s ``on_startup`` lifespan hook fires once at app
    startup, binding the pool to whichever ``DATABASE_URL`` was live at
    that moment. Integration tests rebuild the proposal-store singleton
    against per-test databases (``_reset_proposal_store()`` between
    cases) — each rebuild produces a new ``AsyncConnectionPool`` that
    needs to be opened against its current DSN. Relying on lifespan
    would leave subsequent test pools closed against fresh DSNs.

    Same shape as :class:`core.idempotency._LazyBootstrapPgBackend`.
    The pool open is on the live event loop, runs once per pool, and
    is mutex-guarded so concurrent first-callers don't race the open
    side-effect.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # ``asyncio.Lock`` binds to a loop at construction; defer
        # creation until first use so we attach to the live loop.
        self._open_lock: asyncio.Lock | None = None
        self._opened = False

    async def _ensure_open(self) -> None:
        if self._opened:
            return
        if self._open_lock is None:
            self._open_lock = asyncio.Lock()
        async with self._open_lock:
            if self._opened:
                return
            await self._pool.open()
            self._opened = True
            logger.info("PgProposalStore pool opened on first async use")

    async def put_draft(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().put_draft(*args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().get(*args, **kwargs)

    async def try_reserve_consumption(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().try_reserve_consumption(*args, **kwargs)

    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().commit(*args, **kwargs)

    async def mark_consumed(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().mark_consumed(*args, **kwargs)

    async def discard(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().discard(*args, **kwargs)

    async def release_consumption(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().release_consumption(*args, **kwargs)

    async def get_by_media_buy_id(self, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_open()
        return await super().get_by_media_buy_id(*args, **kwargs)


_LOCK = threading.Lock()
_STORE: _LazyOpenPgProposalStore | None = None
_POOL: AsyncConnectionPool | None = None


def _build_pool() -> AsyncConnectionPool:
    """Build the psycopg3 async pool from ``DATABASE_URL``.

    Sized conservatively: proposal store calls happen on the
    ``get_products`` → ``put_draft`` and ``create_media_buy`` →
    ``try_reserve_consumption`` paths, which are not high-fanout.
    ``max_size=4`` matches :mod:`core.idempotency`'s convention and
    leaves headroom for fork/worker multiplication.

    Pool deliberately doesn't open here — see :func:`get_proposal_store`
    docstring for the event-loop binding rationale.
    """
    from psycopg_pool import AsyncConnectionPool

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL must be set to construct PgProposalStore. "
            "ProposalStore has no in-memory backend — the proposal "
            "lifecycle requires durable storage to survive worker restarts."
        )
    return AsyncConnectionPool(
        url,
        min_size=1,
        max_size=4,
        check=AsyncConnectionPool.check_connection,
        open=False,
    )


def get_proposal_store() -> _LazyOpenPgProposalStore:
    """Return the process-wide proposal store singleton.

    Lazy + thread-safe. The pool is constructed here (sync, doesn't
    talk to the DB) and opened on the first async method call via
    :class:`_LazyOpenPgProposalStore._ensure_open` — which runs on
    whatever event loop dispatches it. This pattern survives test
    re-instantiation (``_reset_proposal_store`` between per-test DBs)
    because every rebuilt singleton opens its own pool against its
    current ``DATABASE_URL`` on first call.
    """
    global _STORE, _POOL
    if _STORE is not None:
        return _STORE

    with _LOCK:
        if _STORE is not None:
            return _STORE

        _POOL = _build_pool()
        # ``table_name="proposals"`` matches the salesagent migration
        # ``t2u3v4w5x6y7_swap_to_pg_proposal_store_schema``. The upstream
        # default is ``adcp_proposal_drafts``, which we deliberately
        # don't use — our table predates the upstream wireup (PR #390
        # created it as ``proposals``) and renaming would force every
        # existing tenant through a no-op rename migration with zero
        # functional gain. Schema columns + indexes match upstream's
        # expected shape; only the table identifier differs.
        #
        # Default ``recipe_decoder`` (``Recipe.model_validate``) is
        # correct — ``SalesAgentProposalManager`` only stores the base
        # ``Recipe`` shape today. If a typed subclass (GAMRecipe, etc.)
        # lands later, supply a ``recipe_decoder=`` here that branches
        # on ``payload.get("recipe_kind")``.
        _STORE = _LazyOpenPgProposalStore(pool=_POOL, table_name="proposals")
        logger.info("PgProposalStore constructed (pool will open on first async use)")
        return _STORE


async def close_proposal_store() -> None:
    """Close the pool at shutdown. Wired into ``serve(on_shutdown=...)``.

    Idempotent — safe to call multiple times (the underlying
    :class:`AsyncConnectionPool` tracks its own open/closed state).
    """
    global _STORE, _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None
        _STORE = None


def reset_for_tests() -> None:
    """Drop the cached singletons. Test infrastructure only.

    Per-test databases (see ``tests/fixtures/integration_db.py``) need
    to rebuild the pool against a fresh ``DATABASE_URL``. The lock is
    re-entered to guarantee no other caller is mid-construction.

    Does NOT await pool close — by design. Per-test teardown runs in
    sync test scope, and the pool's worker tasks are bound to a foreign
    event loop (the production ``serve()`` loop, or the pytest-asyncio
    loop from the prior test). Awaiting close would either deadlock
    (running loop) or orphan the cleanup (closed loop). Process exit
    at end of the pytest run reclaims the underlying sockets. Tests
    that re-use the same process between integration runs and care
    about clean pool shutdown must ``await close_proposal_store()``
    before calling this helper.
    """
    global _STORE, _POOL
    with _LOCK:
        _STORE = None
        _POOL = None
