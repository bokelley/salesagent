"""Unit test for idempotency AsyncConnectionPool's ``check=`` reconnection probe.

Without a ``check`` callback, an idle PgBouncer connection that has been
evicted by ``client_idle_timeout`` is handed back to the caller as a closed
socket — the next operation surfaces as ``ProtocolViolation`` mid-handler
(issue #252). Setting ``check=AsyncConnectionPool.check_connection`` runs a
tiny round-trip on each acquire and reconnects on failure.
"""

import os
from unittest.mock import patch


def test_idempotency_pool_uses_check_connection_callback():
    """``_build_pool`` must wire psycopg_pool's check_connection probe so dead
    sockets are replaced before they're handed to the caller.
    """
    captured: dict = {}

    class FakeAsyncConnectionPool:
        check_connection = "sentinel-check-method"

        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    with (
        patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost:5432/db"}),
        patch("psycopg_pool.AsyncConnectionPool", FakeAsyncConnectionPool),
    ):
        from core.idempotency import _build_pool

        _build_pool()

    assert "check" in captured["kwargs"], "Pool constructed without check= probe"
    assert captured["kwargs"]["check"] is FakeAsyncConnectionPool.check_connection
