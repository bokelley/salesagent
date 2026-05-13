"""Unit tests for the uvicorn.access noise filter.

The filter drops 2xx access-log lines for /mcp[/] and /health (the two
endpoints hit constantly by MCP pollers and Fly health checks) so 1000s
of "200 OK" lines don't bury real signal. 4xx/5xx still surface.
"""

from __future__ import annotations

import logging

import pytest

from src.core.logging_config import UvicornAccessNoiseFilter


def _make_record(message: str) -> logging.LogRecord:
    """Build a LogRecord matching uvicorn.access's rendered message shape."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestUvicornAccessNoiseFilter:
    """Behavioral contract for the filter."""

    @pytest.fixture
    def filter_(self) -> UvicornAccessNoiseFilter:
        return UvicornAccessNoiseFilter()

    @pytest.mark.parametrize(
        "message",
        [
            '172.16.7.138:55934 - "GET /mcp HTTP/1.1" 200 OK',
            '172.16.7.138:55934 - "GET /mcp/ HTTP/1.1" 200 OK',
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 202 Accepted',
            '172.16.7.138:55934 - "POST /mcp/ HTTP/1.1" 200 OK',
            '172.19.13.249:48886 - "GET /health HTTP/1.1" 200 OK',
            '172.16.7.138:0 - "HEAD /mcp HTTP/1.1" 200 OK',
        ],
    )
    def test_drops_2xx_mcp_and_health(self, filter_, message):
        """The polling baseline — /mcp and /health 2xx must be suppressed."""
        record = _make_record(message)
        assert filter_.filter(record) is False, f"Expected to drop: {message!r}"

    @pytest.mark.parametrize(
        "message",
        [
            # 4xx — auth failures, missing routes — must still log.
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 401 Unauthorized',
            '172.16.13.250:39132 - "GET /.well-known/oauth-protected-resource/mcp/ HTTP/1.1" 401 Unauthorized',
            '172.16.7.138:55934 - "GET /mcp HTTP/1.1" 404 Not Found',
            # 5xx — server errors — must still log.
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 500 Internal Server Error',
            '172.16.7.138:55934 - "GET /health HTTP/1.1" 503 Service Unavailable',
        ],
    )
    def test_keeps_non_2xx_on_noisy_paths(self, filter_, message):
        """4xx and 5xx must survive — they're the actual signal."""
        record = _make_record(message)
        assert filter_.filter(record) is True, f"Expected to keep: {message!r}"

    @pytest.mark.parametrize(
        "message",
        [
            # Admin UI traffic — must always log so we can debug tenant flows.
            '127.0.0.1:0 - "POST /tenant/abc/products/add HTTP/1.1" 200 OK',
            '127.0.0.1:0 - "GET /admin/ HTTP/1.1" 200 OK',
            # A2A surface lives at /a2a — never suppress.
            '127.0.0.1:0 - "POST /a2a HTTP/1.1" 200 OK',
            # /mcp-suffix path that's not actually /mcp — don't false-positive.
            '127.0.0.1:0 - "GET /mcp-debug HTTP/1.1" 200 OK',
            # Discovery surface — keep these so the OAuth dance is visible.
            '127.0.0.1:0 - "GET /.well-known/agent-card.json HTTP/1.1" 200 OK',
        ],
    )
    def test_keeps_other_paths(self, filter_, message):
        """Only /mcp[/] and /health are suppressed — every other route logs."""
        record = _make_record(message)
        assert filter_.filter(record) is True, f"Expected to keep: {message!r}"

    def test_keeps_query_strings(self, filter_):
        """Query strings on the noisy paths still get suppressed on 2xx."""
        record = _make_record('127.0.0.1:0 - "GET /health?check=1 HTTP/1.1" 200 OK')
        assert filter_.filter(record) is False

    def test_keeps_non_access_log_messages(self, filter_):
        """The filter is only attached to uvicorn.access but if a stray
        non-matching message hits it, we must not drop it."""
        record = _make_record("Started server process [12345]")
        assert filter_.filter(record) is True
