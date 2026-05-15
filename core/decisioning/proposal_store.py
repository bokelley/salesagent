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

import logging
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adcp.decisioning.pg.proposal_store import PgProposalStore
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STORE: PgProposalStore | None = None
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


def get_proposal_store() -> PgProposalStore:
    """Return the process-wide :class:`PgProposalStore` singleton.

    Lazy + thread-safe. The pool opens on the first async store method
    call, on whatever event loop is running at that point — which means
    it binds to ``serve()``'s loop, not whatever transient loop
    constructed the store. Same rationale as
    :class:`core.idempotency._LazyBootstrapPgBackend`.
    """
    global _STORE, _POOL
    if _STORE is not None:
        return _STORE

    with _LOCK:
        if _STORE is not None:
            return _STORE

        from adcp.decisioning.pg.proposal_store import PgProposalStore

        _POOL = _build_pool()
        # Default ``recipe_decoder`` (``Recipe.model_validate``) is
        # correct — ``SalesAgentProposalManager`` only stores the base
        # ``Recipe`` shape today. If a typed subclass (GAMRecipe, etc.)
        # lands later, supply a ``recipe_decoder=`` here that branches
        # on ``payload.get("recipe_kind")``.
        _STORE = PgProposalStore(pool=_POOL)
        logger.info("PgProposalStore constructed (pool will open on first async use)")
        return _STORE


async def open_proposal_store() -> None:
    """Construct the store (if not already) and open its pool on the
    current event loop. Wired into ``serve(on_startup=...)``.

    Must run on the same loop that later dispatches store method calls
    — psycopg3's :class:`AsyncConnectionPool` binds its worker tasks to
    whichever loop ran ``open()``. Calling ``open()`` from a transient
    bootstrap loop (e.g. ``asyncio.run`` inside a sync factory) would
    leave the workers tied to a closed loop once ``serve()`` takes
    over and the first acquire would hang forever.

    Idempotent — :meth:`AsyncConnectionPool.open` is a no-op if the
    pool is already open.
    """
    store = get_proposal_store()  # ensures _POOL singleton exists
    assert _POOL is not None
    await _POOL.open()
    # Touch ``store`` so the construction visibly succeeds in
    # startup logs (and so static analysis doesn't flag the var as
    # unused).
    logger.info("PgProposalStore pool opened (store=%r)", type(store).__name__)


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
