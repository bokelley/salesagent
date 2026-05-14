"""Tiny helpers for fixed-interval async schedulers.

Extracted to keep :class:`AdapterReportingSyncScheduler` from duplicating
the cancel-and-await pattern that's already in
:class:`DeliveryWebhookScheduler` and :class:`MediaBuyStatusScheduler`.

The existing two schedulers don't yet use this module — their duplication
is grandfathered in (#382 isn't a refactor of those). New schedulers
should call ``cancel_scheduler_task()`` instead of inlining the
try/except/CancelledError dance.
"""

from __future__ import annotations

import asyncio


async def cancel_scheduler_task(task: asyncio.Task | None) -> None:
    """Cancel ``task`` and swallow the resulting :class:`asyncio.CancelledError`.

    A no-op if the task is already done or ``None`` — keeps the caller's
    ``stop()`` idempotent so the lifespan hook can be triggered twice
    (Starlette has fired both startup and shutdown twice in some test
    harnesses and the schedulers shouldn't raise on a redundant stop).
    """
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
