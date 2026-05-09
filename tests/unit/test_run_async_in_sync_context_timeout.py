"""Unit tests for ``run_async_in_sync_context`` timeout behavior.

The bridge from sync (FastMCP tool handler) into async (psycopg / SDK
internals) used to call ``future.result()`` with no timeout. A worker thread
blocked on a half-open database socket would outlive the SDK's tool-call
deadline, hanging the request forever (issue #252). The bridge now enforces a
typed-error timeout so the failure surfaces as a transient AdCPError with a
recovery hint instead of a thread leak.
"""

import asyncio
import time

import pytest

from src.core.exceptions import AdCPServiceUnavailableError
from src.core.validation_helpers import (
    DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS,
    run_async_in_sync_context,
)


class TestRunAsyncInSyncContextTimeout:
    def test_returns_result_under_budget(self):
        async def quick():
            await asyncio.sleep(0)
            return 42

        result = run_async_in_sync_context(quick(), timeout_seconds=2.0)
        assert result == 42

    def test_raises_adcp_service_unavailable_when_outside_running_loop(self):
        """When called from sync code with no running loop, slow coroutine raises typed error."""

        async def slow():
            await asyncio.sleep(5)
            return "never"

        start = time.monotonic()
        with pytest.raises(AdCPServiceUnavailableError) as excinfo:
            run_async_in_sync_context(slow(), timeout_seconds=0.1)
        elapsed = time.monotonic() - start

        assert excinfo.value.recovery == "transient"
        assert "0.1" in excinfo.value.message
        # Should fail fast — well under the 5s the coroutine would have taken.
        assert elapsed < 2.0, f"Timeout did not fire promptly: {elapsed}s"

    def test_raises_adcp_service_unavailable_inside_running_loop(self):
        """When called from inside a running loop (FastMCP path), thread-pool timeout still typed."""

        async def outer():
            # Simulate the production path: an async tool handler that calls
            # sync code which calls ``run_async_in_sync_context`` — the
            # branch that uses a ThreadPoolExecutor because the loop is live.
            async def slow():
                await asyncio.sleep(5)
                return "never"

            # Run sync inside async: scheduled in default loop's executor so
            # ``run_async_in_sync_context`` sees a running loop.
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, lambda: run_async_in_sync_context(slow(), timeout_seconds=0.1))

        start = time.monotonic()
        with pytest.raises(AdCPServiceUnavailableError) as excinfo:
            asyncio.run(outer())
        elapsed = time.monotonic() - start

        assert excinfo.value.recovery == "transient"
        assert elapsed < 2.0, f"Timeout did not fire promptly: {elapsed}s"

    def test_default_timeout_is_under_sdk_tool_call_deadline(self):
        """Default budget must be under typical 30s SDK tool-call deadline."""
        assert DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS < 30.0
        assert DEFAULT_ASYNC_BRIDGE_TIMEOUT_SECONDS > 0

    def test_callers_can_override_timeout(self):
        """Callers can pass a custom timeout (or ``None`` to wait indefinitely)."""

        async def quick():
            return "ok"

        # Override with a tighter budget — still passes because coroutine is instant.
        assert run_async_in_sync_context(quick(), timeout_seconds=0.5) == "ok"

    def test_non_coroutine_input_raises_type_error(self):
        """Pre-existing contract: passing a non-coroutine still raises TypeError."""
        with pytest.raises(TypeError):
            run_async_in_sync_context("not a coroutine")
