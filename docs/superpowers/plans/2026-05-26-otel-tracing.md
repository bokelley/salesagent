# OTEL Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenTelemetry distributed tracing to the salesagent service so trace context from agentic-api propagates through all tool executions and DB queries, with full trace IDs injected into structured log records.

**Architecture:** A thin `telemetry.py` module owns all OTEL SDK setup and is the single on/off switch (keyed on `OTEL_EXPORTER_OTLP_ENDPOINT`). An ASGI middleware reads incoming W3C `traceparent` headers and starts a root span per request. A `@traced` decorator wraps every `_impl()` function to create child spans. SQLAlchemy auto-instrumentation adds DB query spans. The JSON logger injects `trace_id` into every production log record when a span is active.

**Tech Stack:** Python 3.13, Starlette ASGI, SQLAlchemy 2.0, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-sqlalchemy`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add 3 explicit OTEL deps |
| `src/core/telemetry.py` | Create | Provider lifecycle, `get_tracer()`, `init_telemetry()`, `shutdown_telemetry()` |
| `src/core/middleware/tracing.py` | Create | ASGI middleware: reads `traceparent`, starts root span, adds `x-trace-id` response header |
| `src/core/tracing.py` | Create | `@traced` decorator for `_impl()` functions |
| `src/core/logging_config.py` | Modify | Inject `trace_id` into `JSONFormatter.format()` |
| `src/core/startup.py` | Modify | Call `init_telemetry()` and `SQLAlchemyInstrumentor().instrument()` |
| `core/main.py` | Modify | Add `TracingMiddleware` to stack, add `shutdown_telemetry` to `on_shutdown`, apply `@traced` to all `_impl()` imports |
| `tests/unit/test_telemetry.py` | Create | Unit tests for telemetry init/no-op behaviour |
| `tests/unit/test_tracing_decorator.py` | Create | Unit tests for `@traced` decorator |
| `tests/unit/test_tracing_middleware.py` | Create | Unit tests for ASGI tracing middleware |
| `tests/unit/test_logging_trace_id.py` | Create | Unit tests for trace ID injection in `JSONFormatter` |

---

## Task 1: Add OTEL dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the three explicit OTEL dependencies**

In `pyproject.toml`, in the `[project]` `dependencies` list, add after the `logfire>=4.16.0` line:

```toml
    "opentelemetry-sdk>=1.39.0",
    "opentelemetry-exporter-otlp-proto-http>=1.39.0",
    "opentelemetry-instrumentation-sqlalchemy>=0.60b0",
```

- [ ] **Step 2: Sync the lockfile**

```bash
uv sync
```

Expected: resolves without conflicts (these packages are already in `uv.lock` transitively).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: Add explicit OTEL dependencies"
```

---

## Task 2: Create `src/core/telemetry.py`

**Files:**
- Create: `src/core/telemetry.py`
- Create: `tests/unit/test_telemetry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_telemetry.py`:

```python
import os
from unittest.mock import MagicMock, patch

import pytest


def test_init_telemetry_no_op_when_endpoint_not_set(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from src.core.telemetry import init_telemetry, is_tracing_enabled

    init_telemetry()
    assert not is_tracing_enabled()


def test_init_telemetry_enables_when_endpoint_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "test-salesagent")

    with (
        patch("src.core.telemetry.OTLPSpanExporter"),
        patch("src.core.telemetry.TracerProvider"),
        patch("src.core.telemetry.BatchSpanProcessor"),
        patch("src.core.telemetry.trace.set_tracer_provider"),
        patch("src.core.telemetry.propagate.set_global_textmap"),
    ):
        from src.core import telemetry as tel

        # Reset module state so init runs fresh
        tel._tracing_enabled = False
        tel._tracer_provider = None
        tel.init_telemetry()
        assert tel.is_tracing_enabled()


def test_get_tracer_returns_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from src.core import telemetry as tel

    tel._tracing_enabled = False
    tracer = tel.get_tracer("test")
    # NoOpTracer has no active spans
    with tracer.start_as_current_span("test-span") as span:
        assert not span.is_recording()


def test_shutdown_telemetry_no_op_when_disabled(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from src.core import telemetry as tel

    tel._tracing_enabled = False
    # Should not raise
    tel.shutdown_telemetry()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_telemetry.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `src.core.telemetry` does not exist yet.

- [ ] **Step 3: Create `src/core/telemetry.py`**

```python
"""OpenTelemetry provider lifecycle.

Tracing is enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
All other OTEL configuration uses standard SDK env vars:
  OTEL_SERVICE_NAME, OTEL_EXPORTER_OTLP_HEADERS, OTEL_TRACES_SAMPLER, etc.
"""

import logging
import os

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.propagators.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

_tracing_enabled: bool = False
_tracer_provider: TracerProvider | None = None


def is_tracing_enabled() -> bool:
    return _tracing_enabled


def init_telemetry() -> None:
    """Initialise the OTEL tracer provider.

    No-op when OTEL_EXPORTER_OTLP_ENDPOINT is not set.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _tracing_enabled, _tracer_provider

    if _tracing_enabled:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    exporter = OTLPSpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator()])
    )

    _tracer_provider = provider
    _tracing_enabled = True
    logger.info("OpenTelemetry tracing enabled", extra={"endpoint": endpoint})


def shutdown_telemetry() -> None:
    """Flush pending spans and shut down the provider.

    Called during ASGI lifespan shutdown. No-op when tracing is disabled.
    """
    global _tracing_enabled, _tracer_provider

    if not _tracing_enabled or _tracer_provider is None:
        return

    _tracer_provider.shutdown()
    _tracing_enabled = False
    _tracer_provider = None


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for the given instrumentation scope.

    Returns a no-op tracer when tracing is disabled.
    """
    return trace.get_tracer(name)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_telemetry.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Run quality gates**

```bash
make quality
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/core/telemetry.py tests/unit/test_telemetry.py
git commit -m "feat: Add OTEL telemetry provider lifecycle"
```

---

## Task 3: Create `src/core/tracing.py` (`@traced` decorator)

**Files:**
- Create: `src/core/tracing.py`
- Create: `tests/unit/test_tracing_decorator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tracing_decorator.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.tracing import traced


def test_traced_passes_through_sync_result():
    @traced
    def my_impl(identity=None):
        return "result"

    assert my_impl() == "result"


def test_traced_passes_through_async_result():
    @traced
    async def my_impl(identity=None):
        return "async_result"

    result = asyncio.get_event_loop().run_until_complete(my_impl())
    assert result == "async_result"


def test_traced_reraises_exception():
    @traced
    def my_impl(identity=None):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        my_impl()


def test_traced_reraises_async_exception():
    @traced
    async def my_impl(identity=None):
        raise ValueError("async boom")

    with pytest.raises(ValueError, match="async boom"):
        asyncio.get_event_loop().run_until_complete(my_impl())


def test_traced_span_name_strips_impl_suffix():
    recorded_names = []

    mock_span = MagicMock()
    mock_span.__enter__ = lambda s: s
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span = lambda name, **kw: (
        recorded_names.append(name) or mock_span
    )

    with patch("src.core.tracing.get_tracer", return_value=mock_tracer):
        with patch("src.core.tracing.is_tracing_enabled", return_value=True):
            @traced
            def _create_media_buy_impl(identity=None):
                return "ok"

            _create_media_buy_impl()

    assert recorded_names == ["create_media_buy"]


def test_traced_is_noop_when_tracing_disabled():
    call_count = {"n": 0}

    with patch("src.core.tracing.is_tracing_enabled", return_value=False):
        @traced
        def my_impl(identity=None):
            call_count["n"] += 1
            return "result"

        result = my_impl()

    assert result == "result"
    assert call_count["n"] == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_tracing_decorator.py -v
```

Expected: `ImportError` — `src.core.tracing` does not exist yet.

- [ ] **Step 3: Create `src/core/tracing.py`**

```python
"""@traced decorator for _impl() functions.

Creates a child span per function call. No-op when tracing is disabled.
Span name is the function name with the `_impl` suffix stripped.
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any

from opentelemetry.trace import Status, StatusCode

from src.core.telemetry import get_tracer, is_tracing_enabled

logger = logging.getLogger(__name__)

_TRACER_NAME = "salesagent.tools"


def _span_name(func: Callable) -> str:
    name = func.__name__
    if name.endswith("_impl"):
        name = name[: -len("_impl")]
    if name.startswith("_"):
        name = name[1:]
    return name


def traced(func: Callable) -> Callable:
    """Wrap an _impl() function with an OTEL child span.

    Works for both sync and async callables.
    Span name is derived from the function name by stripping leading `_` and trailing `_impl`.
    Sets `salesagent.tenant_id` from the `identity` parameter when present.
    Records exceptions and sets ERROR status on any unhandled exception, then re-raises.
    """
    name = _span_name(func)

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return await func(*args, **kwargs)

            tracer = get_tracer(_TRACER_NAME)
            with tracer.start_as_current_span(name) as span:
                _set_identity_attribute(span, args, kwargs)
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return func(*args, **kwargs)

            tracer = get_tracer(_TRACER_NAME)
            with tracer.start_as_current_span(name) as span:
                _set_identity_attribute(span, args, kwargs)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return sync_wrapper


def _set_identity_attribute(span: Any, args: tuple, kwargs: dict) -> None:
    identity = kwargs.get("identity")
    if identity is None and len(args) >= 2:
        identity = args[1]
    if identity is not None and hasattr(identity, "tenant_id"):
        span.set_attribute("salesagent.tenant_id", str(identity.tenant_id))
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_tracing_decorator.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run quality gates**

```bash
make quality
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/core/tracing.py tests/unit/test_tracing_decorator.py
git commit -m "feat: Add @traced decorator for _impl() functions"
```

---

## Task 4: Create `src/core/middleware/tracing.py`

**Files:**
- Create: `src/core/middleware/tracing.py`
- Create: `tests/unit/test_tracing_middleware.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tracing_middleware.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


async def _call_middleware(middleware, scope, headers=None):
    """Helper: call middleware with a minimal ASGI scope."""
    headers = headers or []
    receive = AsyncMock()
    send_calls = []

    async def send(message):
        send_calls.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": b"",
        "headers": headers,
        **scope,
    }
    await middleware(scope, receive, send)
    return send_calls


@pytest.mark.asyncio
async def test_middleware_passes_through_when_tracing_disabled():
    with patch("src.core.middleware.tracing.is_tracing_enabled", return_value=False):
        from src.core.middleware.tracing import TracingMiddleware

        inner_called = {"n": 0}

        async def inner_app(scope, receive, send):
            inner_called["n"] += 1
            await send({"type": "http.response.start", "status": 200, "headers": []})

        middleware = TracingMiddleware(inner_app)
        await _call_middleware(middleware, {}, headers=[])
        assert inner_called["n"] == 1


@pytest.mark.asyncio
async def test_middleware_extracts_traceparent_header():
    with (
        patch("src.core.middleware.tracing.is_tracing_enabled", return_value=True),
        patch("src.core.middleware.tracing.get_tracer") as mock_get_tracer,
        patch("src.core.middleware.tracing.propagate") as mock_propagate,
    ):
        mock_span = MagicMock()
        mock_span.__enter__ = lambda s: s
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = MagicMock(
            trace_id=0xABCD1234ABCD1234ABCD1234ABCD1234,
            is_valid=True,
        )
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span
        mock_get_tracer.return_value = mock_tracer
        mock_propagate.extract.return_value = {}

        from src.core.middleware.tracing import TracingMiddleware

        response_headers = []

        async def inner_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def capturing_send(message):
            if message["type"] == "http.response.start":
                response_headers.extend(message.get("headers", []))

        middleware = TracingMiddleware(inner_app)
        traceparent = b"00-abcd1234abcd1234abcd1234abcd1234-0102030405060708-01"
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
            "headers": [(b"traceparent", traceparent)],
        }
        await middleware(scope, AsyncMock(), capturing_send)

        mock_propagate.extract.assert_called_once()


@pytest.mark.asyncio
async def test_middleware_skips_non_http_scopes():
    with patch("src.core.middleware.tracing.is_tracing_enabled", return_value=True):
        from src.core.middleware.tracing import TracingMiddleware

        inner_called = {"n": 0}

        async def inner_app(scope, receive, send):
            inner_called["n"] += 1

        middleware = TracingMiddleware(inner_app)
        scope = {"type": "lifespan"}
        await middleware(scope, AsyncMock(), AsyncMock())
        assert inner_called["n"] == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_tracing_middleware.py -v
```

Expected: `ImportError` — `src.core.middleware.tracing` does not exist yet.

- [ ] **Step 3: Create `src/core/middleware/tracing.py`**

```python
"""ASGI middleware that creates a root span per HTTP request.

Reads W3C traceparent from incoming headers to continue a distributed trace.
Adds x-trace-id to response headers for log correlation.
No-op when tracing is disabled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import propagate, trace
from opentelemetry.semconv.trace import SpanAttributes

from src.core.telemetry import get_tracer, is_tracing_enabled

logger = logging.getLogger(__name__)

ASGIApp = Callable
_TRACER_NAME = "salesagent.http"


class TracingMiddleware:
    """Outermost ASGI middleware: one root span per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not is_tracing_enabled():
            await self._app(scope, receive, send)
            return

        headers_list: list[tuple[bytes, bytes]] = scope.get("headers", [])
        carrier = {
            k.decode(): v.decode()
            for k, v in headers_list
        }
        ctx = propagate.extract(carrier)

        tracer = get_tracer(_TRACER_NAME)
        method = scope.get("method", "")
        path = scope.get("path", "")
        span_name = f"{method} {path}".strip()

        with tracer.start_as_current_span(
            span_name,
            context=ctx,
            kind=trace.SpanKind.SERVER,
        ) as span:
            span.set_attribute(SpanAttributes.HTTP_METHOD, method)
            span.set_attribute(SpanAttributes.HTTP_TARGET, path)

            status_code: list[int] = []

            async def send_with_trace_header(message: dict) -> None:
                if message["type"] == "http.response.start":
                    code = message.get("status", 0)
                    status_code.append(code)
                    span.set_attribute(SpanAttributes.HTTP_STATUS_CODE, code)

                    trace_id = _format_trace_id(span)
                    if trace_id:
                        existing = list(message.get("headers", []))
                        existing.append((b"x-trace-id", trace_id.encode()))
                        message = {**message, "headers": existing}

                await send(message)

            try:
                await self._app(scope, receive, send_with_trace_header)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
                raise


def _format_trace_id(span: trace.Span) -> str | None:
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_tracing_middleware.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run quality gates**

```bash
make quality
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/core/middleware/tracing.py tests/unit/test_tracing_middleware.py
git commit -m "feat: Add ASGI tracing middleware with traceparent propagation"
```

---

## Task 5: Inject `trace_id` into `JSONFormatter`

**Files:**
- Modify: `src/core/logging_config.py`
- Create: `tests/unit/test_logging_trace_id.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_logging_trace_id.py`:

```python
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.core.logging_config import JSONFormatter


def test_json_formatter_includes_trace_id_when_span_active():
    mock_ctx = MagicMock()
    mock_ctx.is_valid = True
    mock_ctx.trace_id = 0xABCD1234ABCD1234ABCD1234ABCD1234

    mock_span = MagicMock()
    mock_span.get_span_context.return_value = mock_ctx

    with patch("src.core.logging_config.trace.get_current_span", return_value=mock_span):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        output = json.loads(formatter.format(record))

    assert output["trace_id"] == "abcd1234abcd1234abcd1234abcd1234"


def test_json_formatter_omits_trace_id_when_no_span():
    mock_span = MagicMock()
    mock_span.get_span_context.return_value = MagicMock(is_valid=False)

    with patch("src.core.logging_config.trace.get_current_span", return_value=mock_span):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        output = json.loads(formatter.format(record))

    assert "trace_id" not in output


def test_json_formatter_omits_trace_id_when_otel_not_available():
    with patch("src.core.logging_config.trace", None):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        output = json.loads(formatter.format(record))

    assert "trace_id" not in output
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/unit/test_logging_trace_id.py -v
```

Expected: `FAIL` — `JSONFormatter` does not yet inject `trace_id`.

- [ ] **Step 3: Modify `src/core/logging_config.py`**

Add this import near the top of the file, after the existing stdlib imports:

```python
try:
    from opentelemetry import trace
except ImportError:
    trace = None  # type: ignore[assignment]
```

Then, in the `JSONFormatter.format()` method, add trace ID injection just before the `return json.dumps(log_entry)` line. The existing method ends like this:

```python
        extra_fields = {k: v for k, v in record.__dict__.items() if k not in standard_attrs}
        if extra_fields:
            log_entry["extra"] = extra_fields

        return json.dumps(log_entry)
```

Change it to:

```python
        extra_fields = {k: v for k, v in record.__dict__.items() if k not in standard_attrs}
        if extra_fields:
            log_entry["extra"] = extra_fields

        if trace is not None:
            span = trace.get_current_span()
            ctx = span.get_span_context() if span else None
            if ctx and ctx.is_valid:
                log_entry["trace_id"] = format(ctx.trace_id, "032x")

        return json.dumps(log_entry)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_logging_trace_id.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run quality gates**

```bash
make quality
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/core/logging_config.py tests/unit/test_logging_trace_id.py
git commit -m "feat: Inject trace_id into JSON log records when span is active"
```

---

## Task 6: Wire telemetry init + SQLAlchemy instrumentation into startup

**Files:**
- Modify: `src/core/startup.py`

- [ ] **Step 1: Write the failing test**

Add to a new file `tests/unit/test_startup_telemetry.py`:

```python
from unittest.mock import MagicMock, call, patch


def test_initialize_application_calls_init_telemetry():
    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration"),
        patch("src.core.startup.init_telemetry") as mock_init_tel,
        patch("src.core.startup.instrument_sqlalchemy"),
    ):
        from src.core.startup import initialize_application
        initialize_application()
        mock_init_tel.assert_called_once()


def test_initialize_application_calls_instrument_sqlalchemy():
    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration"),
        patch("src.core.startup.init_telemetry"),
        patch("src.core.startup.instrument_sqlalchemy") as mock_instrument,
    ):
        from src.core.startup import initialize_application
        initialize_application()
        mock_instrument.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_startup_telemetry.py -v
```

Expected: `FAIL` — `init_telemetry` and `instrument_sqlalchemy` are not imported in startup yet.

- [ ] **Step 3: Modify `src/core/startup.py`**

Add two imports after the existing imports:

```python
from src.core.telemetry import init_telemetry


def instrument_sqlalchemy() -> None:
    """Instrument SQLAlchemy engines for OTEL tracing.

    No-op when tracing is disabled — SQLAlchemyInstrumentor checks internally.
    """
    from src.core.telemetry import is_tracing_enabled

    if not is_tracing_enabled():
        return

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except ImportError:
        pass
```

Then in `initialize_application()`, add the two calls after `setup_structured_logging()`:

```python
    try:
        # Setup structured logging FIRST (before any logging calls)
        # This ensures production environments get JSON logs
        setup_structured_logging()

        # Initialise OTEL tracing before any other setup so spans cover startup
        init_telemetry()
        instrument_sqlalchemy()

        logger.info("Initializing Prebid Sales Agent...")
        # ... rest unchanged
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_startup_telemetry.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run quality gates**

```bash
make quality
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/core/startup.py tests/unit/test_startup_telemetry.py
git commit -m "feat: Wire OTEL init and SQLAlchemy instrumentation into application startup"
```

---

## Task 7: Wire `TracingMiddleware` + `shutdown_telemetry` + `@traced` into `core/main.py`

**Files:**
- Modify: `core/main.py`

This task has three sub-parts: middleware, shutdown hook, and `@traced` on all `_impl()` function call sites.

- [ ] **Step 1: Add `TracingMiddleware` to the middleware stack**

In `core/main.py`, add this import near the other middleware imports:

```python
from src.core.middleware.tracing import TracingMiddleware
from src.core.telemetry import is_tracing_enabled, shutdown_telemetry
```

In `_serve_kwargs()`, the `asgi_middleware` list currently starts with `AdminWSGIMount`. Add `TracingMiddleware` as the **first** entry so it is the outermost layer:

```python
    asgi_middleware: list = [
        (TracingMiddleware, {}),
        (AdminWSGIMount, {"wsgi_app": admin_wsgi}),
        (DualCredentialAuditMiddleware, {}),
        (BuyerProtocolOriginGuardMiddleware, {"allowed_origins": allowed_origins}),
    ]
```

Note: `TracingMiddleware` takes no constructor kwargs beyond `app` — passing `{}` is correct for the `(MiddlewareClass, kwargs_dict)` tuple format used by this middleware list (confirmed in `_serve_kwargs()`).

- [ ] **Step 2: Add `shutdown_telemetry` to the `on_shutdown` hook**

The current `on_shutdown` in `_serve_kwargs()` is:

```python
on_shutdown = [_stop_schedulers, close_proposal_store] if include_scheduler else [close_proposal_store]
```

Change to:

```python
on_shutdown = (
    [_stop_schedulers, close_proposal_store, shutdown_telemetry]
    if include_scheduler
    else [close_proposal_store, shutdown_telemetry]
)
```

- [ ] **Step 3: Add `@traced` to all `_impl()` functions at their call sites**

Add this import near the top of `core/main.py`:

```python
from src.core.tracing import traced
```

Then apply `@traced` at the definition site of each `_impl()` function by editing each source file directly. The `_impl()` functions live in `src/core/tools/`:

**`src/core/tools/accounts.py`** — add `@traced` above `_list_accounts_impl` and `_sync_accounts_impl`:
```python
from src.core.tracing import traced

@traced
def _list_accounts_impl(...):
    ...

@traced
async def _sync_accounts_impl(...):
    ...
```

**`src/core/tools/products.py`** — add `@traced` above `_get_products_impl`:
```python
from src.core.tracing import traced

@traced
async def _get_products_impl(...):
    ...
```

**`src/core/tools/media_buy_create.py`** — add `@traced` above `_create_media_buy_impl`:
```python
from src.core.tracing import traced

@traced
async def _create_media_buy_impl(...):
    ...
```

**`src/core/tools/media_buy_update.py`** — add `@traced` above `_update_media_buy_impl`:
```python
from src.core.tracing import traced

@traced
def _update_media_buy_impl(...):
    ...
```

**`src/core/tools/media_buy_list.py`** — add `@traced` above `_get_media_buys_impl`:
```python
from src.core.tracing import traced

@traced
def _get_media_buys_impl(...):
    ...
```

**`src/core/tools/signals.py`** — add `@traced` above `_get_signals_impl` and `_activate_signal_impl`:
```python
from src.core.tracing import traced

@traced
async def _get_signals_impl(...):
    ...

@traced
async def _activate_signal_impl(...):
    ...
```

**`src/core/tools/creatives/_sync.py`** — add `@traced` above `_sync_creatives_impl`:
```python
from src.core.tracing import traced

@traced
def _sync_creatives_impl(...):
    ...
```

**Find `_get_media_buy_delivery_impl`, `_update_performance_index_impl`, `_list_creative_formats_impl`** — grep for these names and add `@traced` above each one in the same pattern.

```bash
grep -rn "def _get_media_buy_delivery_impl\|def _update_performance_index_impl\|def _list_creative_formats_impl" src/core/tools/
```

Add `@traced` and `from src.core.tracing import traced` to each file found.

- [ ] **Step 4: Run quality gates**

```bash
make quality
```

Expected: no new errors.

- [ ] **Step 5: Run unit tests**

```bash
uv run pytest tests/unit/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/main.py src/core/tools/
git commit -m "feat: Add TracingMiddleware, shutdown hook, and @traced on all _impl() functions"
```

---

## Task 8: Smoke test end-to-end with Docker

- [ ] **Step 1: Start the local stack**

```bash
docker compose up -d
```

Wait ~15 seconds for the app to boot.

- [ ] **Step 2: Send a test request without OTEL configured**

```bash
curl -s http://localhost:8000/health
```

Expected: `200 OK`. No tracing errors in logs — tracing should be disabled silently.

- [ ] **Step 3: Verify tracing is disabled without endpoint**

```bash
docker compose logs admin-ui 2>&1 | grep -i "otel\|tracing\|telemetry"
```

Expected: no OTEL log lines (tracing is disabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is not set).

- [ ] **Step 4: Run the full test suite**

```bash
./run_all_tests.sh
```

Expected: all suites pass. JSON results saved in `test-results/`.

- [ ] **Step 5: Stop the stack**

```bash
docker compose down
```

- [ ] **Step 6: Commit any fixups from smoke testing**

```bash
git add -p  # stage only intentional changes
git commit -m "fix: Address smoke test findings in OTEL tracing"
```

(Skip this step if no changes were needed.)

---

## Environment Variable Reference for Scope3 Deployment

Add these to the `embedded-sales-agent` Helm values / GKE deployment config:

```yaml
env:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "https://your-otel-collector/v1/traces"
  - name: OTEL_SERVICE_NAME
    value: "salesagent"
  - name: OTEL_EXPORTER_OTLP_HEADERS
    value: "Authorization=Bearer <token>"
  - name: OTEL_TRACES_SAMPLER
    value: "parentbased_always_on"
```

With `parentbased_always_on`, every request from agentic-api that carries a `traceparent` header will be traced through to the sales agent. Cold requests (no incoming trace context) also get their own spans.
