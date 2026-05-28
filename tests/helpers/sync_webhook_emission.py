"""Test helpers for ``src.admin.services.sync_webhook_emission``.

Keeps the snapshot shape + listener-invocation boilerplate out of the
test file so pylint's R0801 duplication detector doesn't flag near-
identical snapshot dicts as new violations.
"""

from __future__ import annotations

from typing import Any

from src.admin.services.sync_webhook_emission import _PENDING_KEY, _flush


def make_snapshot(**overrides: Any) -> dict[str, Any]:
    """Snapshot of a ``status=completed`` SyncJob with all the fields the
    listener emits.

    Equivalent to what ``_capture`` would produce against a real row.
    Pass ``**overrides`` to customize individual keys; the rest stay at
    sensible defaults (None where not load-bearing). Centralized so
    tests don't repeat the literal dict and trip pylint's R0801
    duplication detector.
    """
    base = {
        "_status": "completed",
        "_old_status": None,
        "_old_completed_at": None,
        "_old_error_message": None,
        "_health_change_payload": None,
        "tenant_id": "tnt_x",
        "sync_run_id": "sync_test",
        "sync_type": "inventory",
        "adapter_type": "google_ad_manager",
        "started_at": None,
        "completed_at": None,
        "summary": None,
        "error_message": None,
        "triggered_by": None,
        "triggered_by_id": None,
        "item_count": None,
    }
    base.update(overrides)
    return base


class FakeListenerSession:
    """Stand-in for a SQLAlchemy session carrying a captured snapshot.

    Only the ``info`` dict is needed — the listener reads
    ``session.info[_PENDING_KEY]`` and pops it.
    """

    def __init__(self, snapshots: list[dict[str, Any]] | None = None) -> None:
        self.info: dict[str, Any] = {_PENDING_KEY: snapshots if snapshots is not None else [make_snapshot()]}


def assert_flush_enqueues_without_db_work(fake: FakeListenerSession) -> None:
    """Drive ``_flush`` against a fake session, assert the snapshot is
    moved from ``session.info`` and ``_flush`` returned without raising.

    The key invariant is that ``_flush`` does no in-line DB work — if it
    had, the missing engine would have raised through ``_flush``. We
    don't assert on queue size because the daemon dispatcher may drain
    it before our check (race).
    """
    # If _flush opened a RawSession or called get_engine, this would
    # raise (no real engine bound for this fake session). The new
    # architecture means it only does dict pops + queue puts.
    _flush(fake)

    # Snapshot was drained from session.info (whether enqueued or dropped
    # — both are valid outcomes for the listener's promise of not
    # raising).
    assert fake.info.get(_PENDING_KEY) is None
