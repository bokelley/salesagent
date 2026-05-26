# OTEL Tracing Design

## Overview

Add OpenTelemetry distributed tracing to the salesagent service so that trace context propagated from `agentic-api` (via W3C `traceparent` headers) is captured and continued here. This enables full end-to-end visibility: a trace started in agentic-api flows through the embedded-sales-agent deployment of this service, capturing request spans, tool execution spans, and database query spans.

Tracing is entirely opt-in: it activates only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Open source users who don't configure OTEL see zero overhead and zero behavioral change.

---

## Span Hierarchy

A single agent tool call produces the following trace structure:

```
[HTTP POST /mcp]                       ← request span (parent from agentic-api via traceparent)
  └─ [create_media_buy]                ← _impl() span (child)
       ├─ [SELECT salesagent.products] ← SQLAlchemy span (grandchild)
       └─ [INSERT salesagent.media_buys]
```

The same structure applies to A2A and admin REST requests.

---

## Configuration

Tracing is enabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. All configuration uses standard OTEL SDK environment variables — no custom scheme.

| Env Var | Required | Purpose | Default |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Yes (enables tracing) | OTLP collector endpoint | unset (disabled) |
| `OTEL_SERVICE_NAME` | No | Service name in traces | `salesagent` |
| `OTEL_EXPORTER_OTLP_HEADERS` | No | Auth headers for exporter | unset |
| `OTEL_TRACES_SAMPLER` | No | Sampling strategy | `parentbased_always_on` |
| `OTEL_TRACES_SAMPLER_ARG` | No | Sampler argument | `1.0` |

`parentbased_always_on` means: if agentic-api sends a `traceparent` header, this service always participates in that trace. If no incoming trace context exists, spans are still created (useful for standalone deployments).

For Scope3's internal deployment, these vars are added to the `embedded-sales-agent` Helm values. Open source users point `OTEL_EXPORTER_OTLP_ENDPOINT` at their backend of choice (Grafana, Jaeger, Honeycomb, etc.).

---

## Components

### `src/core/telemetry.py` — Provider Lifecycle

Single module that owns all OTEL SDK setup. Responsible for:

- Checking `OTEL_EXPORTER_OTLP_ENDPOINT` and returning early (no-op) if unset
- Configuring the `OTLPSpanExporter` (HTTP/protobuf)
- Setting `TracerProvider` as the global provider
- Registering the W3C `TraceContextPropagator` globally
- Exposing `init_telemetry()` and `shutdown_telemetry()` functions
- Exposing a `get_tracer()` helper used by the decorator and middleware

Everything else in the codebase imports from this module — no other file touches the OTEL SDK directly.

### `src/core/middleware/tracing.py` — ASGI Request Spans

Starlette middleware added as the outermost layer of the middleware stack in `core/main.py` (before `DualCredentialAuditMiddleware`). When tracing is disabled, this middleware is not added to the stack at all.

Per request:
1. Extracts `traceparent` (and `tracestate`) from incoming headers using the global W3C propagator
2. Starts a server span with the extracted context as parent
3. Sets span attributes: `http.method`, `http.target`, `http.status_code`
4. Adds `x-trace-id` response header (trace ID in hex) for correlation with agentic-api logs
5. Records exceptions on the span if an unhandled error propagates
6. Ends the span in a `finally` block

### `src/core/tracing.py` — `@traced` Decorator

A single decorator for `_impl()` functions. Works on both sync and async callables.

When active:
- Creates a child span named after the function (strips `_impl` suffix: `_create_media_buy_impl` → `create_media_buy`)
- Sets `salesagent.tool` attribute from the span name
- Sets `salesagent.tenant_id` from the `identity` parameter if present
- On `AdCPError` or any unhandled exception: records the exception on the span and sets status to `ERROR`, then re-raises — does not swallow exceptions
- Ends the span in a `finally` block

When tracing is disabled:
- `@traced` returns the original function unchanged — zero runtime overhead

Applied to all `_impl()` functions in `src/core/main.py`:
- `_list_accounts_impl`
- `_sync_accounts_impl`
- `_list_creative_formats_impl`
- `_create_media_buy_impl`
- `_get_media_buy_delivery_impl`
- `_get_media_buys_impl`
- `_update_media_buy_impl`
- `_update_performance_index_impl`
- `_get_products_impl`
- `_get_signals_impl`
- `_activate_signal_impl`
- `_sync_creatives_impl`

### `src/core/logging_config.py` — Trace ID in Log Records

The existing `JSONFormatter.format()` method is modified to inject `trace_id` into every JSON log record when a span is active:

```python
from opentelemetry import trace as otel_trace

# inside JSONFormatter.format():
span = otel_trace.get_current_span()
ctx = span.get_span_context() if span else None
if ctx and ctx.is_valid:
    log_record["trace_id"] = format(ctx.trace_id, "032x")
```

This is a no-op when tracing is disabled (no active span, no key added). In GKE Cloud Logging, every log line emitted during a traced request carries `jsonPayload.trace_id`, enabling `jsonPayload.trace_id="<id>"` queries to surface all log lines for a given trace.

### `src/core/startup.py` — SQLAlchemy Instrumentation + Telemetry Init

`initialize_application()` gains two additions:

1. `init_telemetry()` called after `setup_structured_logging()` — sets up the tracer provider
2. `SQLAlchemyInstrumentor().instrument()` called after telemetry init — patches all SQLAlchemy engines to emit child spans under the active span

Both calls are conditional on tracing being enabled and are no-ops otherwise.

Telemetry shutdown is registered with the ASGI lifespan handler to flush pending spans before process exit.

---

## Dependencies

Add to `pyproject.toml` (already in `uv.lock` via Logfire transitive deps, need to be explicit):

```
opentelemetry-sdk>=1.39.0
opentelemetry-exporter-otlp-proto-http>=1.39.0
opentelemetry-instrumentation-sqlalchemy>=0.60b0
```

`opentelemetry-api` is already an explicit dependency — no change needed.

---

## File Changeset

| File | Change |
|---|---|
| `src/core/telemetry.py` | New — provider lifecycle |
| `src/core/middleware/tracing.py` | New — ASGI middleware |
| `src/core/tracing.py` | New — `@traced` decorator |
| `src/core/logging_config.py` | Modify — inject `trace_id` into `JSONFormatter` |
| `src/core/startup.py` | Modify — call `init_telemetry()` and `SQLAlchemyInstrumentor` |
| `core/main.py` | Modify — add `TracingMiddleware` to stack, `@traced` on all `_impl()` functions |
| `pyproject.toml` | Modify — add 3 explicit OTEL deps |

---

## What Is Not In Scope

- Tracing adapter calls to GAM (external HTTP calls via GAM SDK) — can be added later with `opentelemetry-instrumentation-urllib3` or similar
- Metrics (Prometheus client is already present for that)
- Log-trace correlation beyond `trace_id` injection (e.g. Cloud Trace integration format)
- Baggage propagation
