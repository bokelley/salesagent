"""Unit tests for database session global state helpers."""

import time

from src.core.database import database_session


def test_reset_engine_clears_health_circuit_breaker():
    database_session._is_healthy = False
    database_session._last_health_check = time.time()

    database_session.reset_engine()

    assert database_session._is_healthy is True
    assert database_session._last_health_check == 0.0
