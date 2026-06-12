"""Sentry error reporting configuration."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

SentryLevel = Literal["fatal", "critical", "error", "warning", "info", "debug"]

_initialized = False
_REQUEST_CORRELATION_HEADERS: tuple[tuple[str, str], ...] = (
    ("x-request-id", "request_id"),
    ("x-inventory-source-id", "inventory_source_id"),
    ("traceparent", "traceparent"),
)
_SAFE_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "host",
    "traceparent",
    "user-agent",
    "x-inventory-source-id",
    "x-request-id",
}
_SENSITIVE_BREADCRUMB_TERMS = (
    "authorization",
    "api-key",
    "api_key",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "service_account",
    "set-cookie",
    "token",
)
_URL_WITH_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]+")
_RELATIVE_URL_WITH_QUERY_RE = re.compile(r"((?:/[^\s?]+)+)\?[^\s\"']+")
_PATH_WITH_QUERY_RE = re.compile(r"((?:^|[\s\"'])/[^\s?]+)\?[^\s\"']+")
_URL_KEY_RE = re.compile(r"(?i)(url\s*[:=]\s*)([^,\s)]+)")
_HOST_KEY_RE = re.compile(r"(?i)(host\s*[:=]\s*)(['\"]?)[^,'\")\s]+(['\"]?)")
_BODY_KEY_RE = re.compile(r"(?i)(^|[_.-])(body|payload)(?:$|[_.-])")
_REQUEST_BODY_RE = re.compile(r"(?i)\b(request\s+body|body=|payload=)")


def _env_optional_float(name: str, *, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using default %s", name, raw, default)
        return default
    if value < 0:
        logger.warning("%s=%r is negative; using default %s", name, raw, default)
        return default
    return value


def _release_name() -> str:
    from src.core.version import get_git_sha, get_version

    version = get_version()
    sha = get_git_sha()
    suffix = f"+{sha}" if sha else ""
    return f"adcp-sales-agent@{version}{suffix}"


def _request_headers(event: dict[str, Any]) -> dict[str, Any]:
    request = event.get("request")
    if not isinstance(request, dict):
        return {}
    headers = request.get("headers")
    return headers if isinstance(headers, dict) else {}


def _get_header(headers: dict[str, Any], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            text = str(value).strip()
            if text:
                return text[:200]
    return None


def _add_request_correlation_tags(event: dict[str, Any]) -> None:
    headers = _request_headers(event)
    if not headers:
        return
    tags = event.setdefault("tags", {})
    if not isinstance(tags, dict):
        tags = {}
        event["tags"] = tags
    for header, tag in _REQUEST_CORRELATION_HEADERS:
        value = _get_header(headers, header)
        if value:
            tags.setdefault(tag, value)


def _scrub_request(event: dict[str, Any]) -> None:
    request = event.get("request")
    if not isinstance(request, dict):
        return
    request.pop("data", None)
    request.pop("query_string", None)
    url = request.get("url")
    if isinstance(url, str):
        request["url"] = url.split("?", 1)[0]
    headers = request.get("headers")
    if not isinstance(headers, dict):
        return
    request["headers"] = {key: value for key, value in headers.items() if key.lower() in _SAFE_REQUEST_HEADERS}


def _scrub_exception(event: dict[str, Any]) -> None:
    exception = event.get("exception")
    if not isinstance(exception, dict):
        return
    values = exception.get("values")
    if not isinstance(values, list):
        return
    for value in values:
        if not isinstance(value, dict):
            continue
        exception_value = value.get("value")
        if isinstance(exception_value, str):
            value["value"] = (
                "[redacted]"
                if _contains_sensitive_breadcrumb_text(exception_value)
                else _strip_query_from_text(exception_value)
            )
        stacktrace = value.get("stacktrace")
        if not isinstance(stacktrace, dict):
            continue
        frames = stacktrace.get("frames")
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def _strip_query_from_text(value: str) -> str:
    value = _URL_WITH_QUERY_RE.sub(r"\1", value)
    value = _RELATIVE_URL_WITH_QUERY_RE.sub(r"\1", value)
    value = _PATH_WITH_QUERY_RE.sub(r"\1", value)
    value = _URL_KEY_RE.sub(r"\1[url]", value)
    return _HOST_KEY_RE.sub(r"\1\2[host]\3", value)


def _contains_sensitive_breadcrumb_text(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if _BODY_KEY_RE.search(key_text):
                return True
            if any(term in key_text for term in _SENSITIVE_BREADCRUMB_TERMS):
                return True
            if _contains_sensitive_breadcrumb_text(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_breadcrumb_text(item) for item in value)
    text = str(value).lower()
    if _REQUEST_BODY_RE.search(text):
        return True
    return any(term in text for term in _SENSITIVE_BREADCRUMB_TERMS)


def _scrub_breadcrumb_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[url]" if key.lower() in {"href", "url"} and isinstance(item, str) else _scrub_breadcrumb_urls(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_breadcrumb_urls(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_breadcrumb_urls(item) for item in value)
    if isinstance(value, str):
        return _strip_query_from_text(value)
    return value


def _before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop breadcrumbs that may carry secrets from existing log messages."""
    del hint
    if _contains_sensitive_breadcrumb_text(crumb):
        return None
    return _scrub_breadcrumb_urls(crumb)


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop known noisy disconnect events before they leave the process."""
    exc_info = hint.get("exc_info")
    exc_type = exc_info[0] if exc_info else None
    if exc_type is not None and exc_type.__name__ in {"ClientDisconnect", "BrokenPipeError"}:
        return None
    if event.get("level") == "warning" and "exception" not in event:
        return None
    _scrub_request(event)
    _scrub_exception(event)
    _add_request_correlation_tags(event)
    return event


def _before_send_transaction(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Scrub request/span data from sampled transactions."""
    _scrub_request(event)
    spans = event.get("spans")
    if not isinstance(spans, list):
        return event
    for span in spans:
        if not isinstance(span, dict):
            continue
        data = span.get("data")
        if not isinstance(data, dict):
            continue
        for key in list(data):
            if key.startswith("http.request.header.") or key in {"http.query", "http.request.body.data"}:
                data.pop(key, None)
        url_full = data.get("url.full")
        if isinstance(url_full, str):
            data["url.full"] = url_full.split("?", 1)[0]
    return event


def is_sentry_configured() -> bool:
    return bool(os.environ.get("SENTRY_DSN", "").strip())


def initialize_sentry() -> bool:
    """Initialize Sentry once when SENTRY_DSN is configured."""
    global _initialized

    if not is_sentry_configured():
        return False
    if _initialized:
        return True

    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    if sentry_sdk.is_initialized():
        _initialized = True
        return True

    traces_sample_rate = _env_optional_float("SENTRY_TRACES_SAMPLE_RATE", default=None)
    init_kwargs: dict[str, Any] = {
        "dsn": os.environ["SENTRY_DSN"].strip(),
        "environment": os.environ.get("SENTRY_ENVIRONMENT") or os.environ.get("ENVIRONMENT", "development"),
        "release": _release_name(),
        "sample_rate": 1.0,
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "debug": False,
        "shutdown_timeout": 2.0,
        "before_send": _before_send,
        "before_send_transaction": _before_send_transaction,
        "before_breadcrumb": _before_breadcrumb,
        "integrations": [
            LoggingIntegration(level=logging.WARNING, event_level=None),
            FlaskIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="url"),
        ],
    }
    if traces_sample_rate is not None:
        init_kwargs["traces_sample_rate"] = traces_sample_rate

    try:
        sentry_sdk.init(**init_kwargs)
    except Exception:
        logger.warning("Sentry initialization failed; continuing without telemetry", exc_info=True)
        return False
    _initialized = True
    logger.info(
        "Sentry error reporting enabled",
        extra={"environment": init_kwargs["environment"], "release": init_kwargs["release"]},
    )
    return True


def _set_scope_context(
    scope: Any,
    *,
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    for key, value in (tags or {}).items():
        if value is not None:
            scope.set_tag(key, str(value))
    for key, value in (extra or {}).items():
        if value is not None:
            scope.set_extra(key, value)


def _operation_tags(area: str, operation: str, tags: dict[str, Any] | None) -> dict[str, Any]:
    merged_tags = {"area": area, "operation": operation}
    if tags:
        merged_tags.update(tags)
    return merged_tags


def capture_exception(
    exc: BaseException,
    *,
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Capture an exception with scoped context when SENTRY_DSN is configured."""
    if not is_sentry_configured():
        return None
    try:
        if not _initialized and not initialize_sentry():
            return None

        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            _set_scope_context(scope, tags=tags, extra=extra)
            event_id = sentry_sdk.capture_exception(exc)
            return str(event_id) if event_id is not None else None
    except Exception:
        logger.warning("Sentry exception capture failed; continuing without telemetry", exc_info=True)
        return None


def capture_message(
    message: str,
    *,
    level: SentryLevel = "error",
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Capture a scoped message when SENTRY_DSN is configured."""
    if not is_sentry_configured():
        return None
    try:
        if not _initialized and not initialize_sentry():
            return None

        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            _set_scope_context(scope, tags=tags, extra=extra)
            event_id = sentry_sdk.capture_message(message, level=level)
            return str(event_id) if event_id is not None else None
    except Exception:
        logger.warning("Sentry message capture failed; continuing without telemetry", exc_info=True)
        return None


def capture_operation_exception(
    exc: BaseException,
    *,
    area: str,
    operation: str,
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Capture an exception with normalized operation tags."""
    return capture_exception(
        exc,
        tags=_operation_tags(area, operation, tags),
        extra=extra,
    )


def capture_operation_message(
    message: str,
    *,
    area: str,
    operation: str,
    level: SentryLevel = "error",
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Capture a non-exception failure with normalized operation tags."""
    return capture_message(
        message,
        level=level,
        tags=_operation_tags(area, operation, tags),
        extra=extra,
    )


def flush_sentry() -> None:
    """Flush pending Sentry events if the SDK is active."""
    if not _initialized:
        return

    import sentry_sdk

    sentry_sdk.flush(timeout=2.0)
