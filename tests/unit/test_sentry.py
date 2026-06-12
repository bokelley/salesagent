import logging
from unittest.mock import ANY, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_sentry_state():
    from src.core import sentry

    sentry._initialized = False
    yield
    sentry._initialized = False


def test_initialize_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    from src.core import sentry

    assert sentry.initialize_sentry() is False


def test_initialize_sentry_configures_error_reporting(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_GIT_SHA", "abcdef123456")

    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    from src.core import sentry

    with (
        patch("sentry_sdk.is_initialized", return_value=False),
        patch("sentry_sdk.init") as mock_init,
    ):
        assert sentry.initialize_sentry() is True

    kwargs = mock_init.call_args.kwargs
    assert kwargs["dsn"] == "https://public@example.com/1"
    assert kwargs["environment"] == "production"
    assert kwargs["release"].startswith("adcp-sales-agent@")
    assert kwargs["release"].endswith("+abcdef1")
    assert kwargs["sample_rate"] == 1.0
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["debug"] is False
    assert kwargs["shutdown_timeout"] == 2.0
    assert kwargs["before_send_transaction"] is sentry._before_send_transaction
    assert kwargs["before_breadcrumb"] is sentry._before_breadcrumb
    assert "traces_sample_rate" not in kwargs

    integrations = kwargs["integrations"]
    assert any(isinstance(integration, LoggingIntegration) for integration in integrations)
    assert any(isinstance(integration, FlaskIntegration) for integration in integrations)
    assert any(isinstance(integration, StarletteIntegration) for integration in integrations)

    logging_integration = next(
        integration for integration in integrations if isinstance(integration, LoggingIntegration)
    )
    assert logging_integration._handler is None
    assert logging_integration._breadcrumb_handler.level == logging.WARNING


def test_initialize_sentry_fails_open_when_sdk_rejects_config(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "not-a-dsn")

    from src.core import sentry

    with (
        patch("sentry_sdk.is_initialized", return_value=False),
        patch("sentry_sdk.init", side_effect=RuntimeError("BadDsn")) as mock_init,
    ):
        assert sentry.initialize_sentry() is False

    mock_init.assert_called_once_with(
        dsn="not-a-dsn",
        environment=ANY,
        release=ANY,
        sample_rate=1.0,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        debug=False,
        shutdown_timeout=2.0,
        before_send=sentry._before_send,
        before_send_transaction=sentry._before_send_transaction,
        before_breadcrumb=sentry._before_breadcrumb,
        integrations=ANY,
    )
    assert sentry._initialized is False


def test_initialize_sentry_reads_optional_trace_sample_rate(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")

    from src.core import sentry

    with (
        patch("sentry_sdk.is_initialized", return_value=False),
        patch("sentry_sdk.init") as mock_init,
    ):
        assert sentry.initialize_sentry() is True

    kwargs = mock_init.call_args.kwargs
    assert kwargs["traces_sample_rate"] == 0.25
    assert "profiles_sample_rate" not in kwargs
    assert kwargs["sample_rate"] == 1.0
    assert kwargs["send_default_pii"] is False


def test_before_send_drops_client_disconnect_events():
    from src.core.sentry import _before_send

    class ClientDisconnect(Exception):
        pass

    assert _before_send({"event_id": "1"}, {"exc_info": (ClientDisconnect, ClientDisconnect(), None)}) is None


def test_before_send_drops_warning_messages_without_exceptions():
    from src.core.sentry import _before_send

    assert _before_send({"event_id": "1", "level": "warning"}, {}) is None


def test_before_send_keeps_warning_events_with_exceptions():
    from src.core.sentry import _before_send

    event = {"event_id": "1", "level": "warning", "exception": {"values": []}}
    assert _before_send(event, {}) is event


def test_before_send_adds_request_correlation_tags():
    from src.core.sentry import _before_send

    event = {
        "event_id": "1",
        "level": "error",
        "request": {
            "headers": {
                "X-Request-ID": "req-123",
                "X-Inventory-Source-ID": "invsrc-20",
                "TraceParent": "00-abcd1234abcd1234abcd1234abcd1234-0102030405060708-01",
            }
        },
    }

    assert _before_send(event, {}) is event
    assert event["tags"]["request_id"] == "req-123"
    assert event["tags"]["inventory_source_id"] == "invsrc-20"
    assert event["tags"]["traceparent"] == "00-abcd1234abcd1234abcd1234abcd1234-0102030405060708-01"
    assert event["request"]["headers"] == {
        "X-Request-ID": "req-123",
        "X-Inventory-Source-ID": "invsrc-20",
        "TraceParent": "00-abcd1234abcd1234abcd1234abcd1234-0102030405060708-01",
    }


def test_before_send_scrubs_sensitive_request_headers_and_body():
    from src.core.sentry import _before_send

    event = {
        "event_id": "1",
        "level": "error",
        "request": {
            "url": "https://agent.example/admin/auth/callback?code=oauth-code&state=oauth-state",
            "query_string": "code=oauth-code&state=oauth-state",
            "headers": {
                "User-Agent": "pytest",
                "X-Tenant-Management-API-Key": "management-secret",
                "x-adcp-auth": "buyer-token",
                "X-Identity-Email": "user@example.com",
                "X-Push-Notification-Credentials": "push-secret",
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Request-ID": "req-123",
            },
            "data": {
                "adapter": {
                    "service_account_key_json": '{"private_key":"secret"}',
                    "api_token": "secret",
                }
            },
        },
    }

    assert _before_send(event, {}) is event
    assert event["request"]["headers"] == {
        "User-Agent": "pytest",
        "X-Request-ID": "req-123",
    }
    assert event["request"]["url"] == "https://agent.example/admin/auth/callback"
    assert "query_string" not in event["request"]
    assert "data" not in event["request"]


def test_before_send_scrubs_exception_values_and_frame_vars():
    from src.core.sentry import _before_send

    event = {
        "event_id": "1",
        "level": "error",
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": "GET https://vendor.example/callback?code=oauth-code&state=oauth-state",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "tenant_management_api.py",
                                "function": "adapter_probe",
                                "vars": {"service_account_key_json": '{"private_key":"secret"}'},
                            }
                        ]
                    },
                },
                {
                    "type": "ValueError",
                    "value": "adapter failed with api_token=secret",
                    "stacktrace": {"frames": [{"filename": "adapter.py", "vars": {"password": "secret"}}]},
                },
            ]
        },
    }

    assert _before_send(event, {}) is event

    exceptions = event["exception"]["values"]
    assert exceptions[0]["value"] == "GET https://vendor.example/callback"
    assert "vars" not in exceptions[0]["stacktrace"]["frames"][0]
    assert exceptions[1]["value"] == "[redacted]"
    assert "vars" not in exceptions[1]["stacktrace"]["frames"][0]


def test_before_send_transaction_scrubs_request_and_span_secrets():
    from src.core.sentry import _before_send_transaction

    event = {
        "event_id": "1",
        "request": {
            "url": "https://agent.example/mcp/?token=secret",
            "query_string": "token=secret",
            "headers": {
                "X-Request-ID": "req-123",
                "x-adcp-auth": "buyer-token",
                "X-Identity-Email": "user@example.com",
            },
        },
        "spans": [
            {
                "data": {
                    "http.request.header.x-adcp-auth": "buyer-token",
                    "http.request.header.x-tenant-management-api-key": "management-secret",
                    "http.query": "code=oauth-code&state=oauth-state",
                    "http.request.body.data": '{"service_account_key_json":"secret"}',
                    "url.full": "https://agent.example/admin/auth/callback?code=oauth-code",
                    "http.response.status_code": 500,
                }
            }
        ],
    }

    assert _before_send_transaction(event, {}) is event
    assert event["request"]["url"] == "https://agent.example/mcp/"
    assert event["request"]["headers"] == {"X-Request-ID": "req-123"}
    assert "query_string" not in event["request"]
    span_data = event["spans"][0]["data"]
    assert "http.request.header.x-adcp-auth" not in span_data
    assert "http.request.header.x-tenant-management-api-key" not in span_data
    assert "http.query" not in span_data
    assert "http.request.body.data" not in span_data
    assert span_data["url.full"] == "https://agent.example/admin/auth/callback"
    assert span_data["http.response.status_code"] == 500


def test_before_send_preserves_explicit_request_correlation_tags():
    from src.core.sentry import _before_send

    event = {
        "event_id": "1",
        "level": "error",
        "tags": {"request_id": "explicit-req"},
        "request": {"headers": {"x-request-id": "header-req"}},
    }

    assert _before_send(event, {}) is event
    assert event["tags"]["request_id"] == "explicit-req"


@pytest.mark.parametrize(
    "crumb",
    [
        {"category": "logging", "message": "Set-Cookie: session=secret"},
        {"category": "logging", "message": "Approximated token response: {'access_token': 'secret'}"},
        {"category": "logging", "data": {"Authorization": "Bearer secret"}},
        {"category": "logging", "data": {"service_account_key_json": '{"private_key":"secret"}'}},
        {"category": "logging", "data": {"body": '{"email":"user@example.com"}'}},
        {"category": "logging", "data": {"request_payload": '{"email":"user@example.com"}'}},
        {"category": "logging", "message": 'request body: {"email":"user@example.com"}'},
        {"category": "logging", "message": 'body={"email":"user@example.com"}'},
    ],
)
def test_before_breadcrumb_drops_sensitive_log_content(crumb):
    from src.core.sentry import _before_breadcrumb

    assert _before_breadcrumb(crumb, {}) is None


def test_before_breadcrumb_strips_query_strings_from_urls():
    from src.core.sentry import _before_breadcrumb

    crumb = {
        "category": "httplib",
        "message": "GET https://agent.example/admin/auth/callback?code=oauth-code&state=oauth-state",
        "data": {"url": "https://agent.example/admin/auth/callback?code=oauth-code&state=oauth-state"},
    }

    scrubbed = _before_breadcrumb(crumb, {})

    assert scrubbed == {
        "category": "httplib",
        "message": "GET https://agent.example/admin/auth/callback",
        "data": {"url": "[url]"},
    }


def test_before_breadcrumb_scrubs_transport_host_and_url_patterns():
    from src.core.sentry import _before_breadcrumb

    crumb = {
        "category": "logging",
        "message": "HTTPSConnectionPool(host='internal.vendor.local', port=443): url: /v1/accounts?email=user@example.com",
        "data": {
            "reason": 'HTTPConnectionPool(host="10.0.0.5", port=80)',
            "url": "/v1/accounts?email=user@example.com",
        },
    }

    scrubbed = _before_breadcrumb(crumb, {})

    assert scrubbed == {
        "category": "logging",
        "message": "HTTPSConnectionPool(host='[host]', port=443): url: [url]",
        "data": {
            "reason": 'HTTPConnectionPool(host="[host]", port=80)',
            "url": "[url]",
        },
    }


def test_before_breadcrumb_scrubs_loose_host_url_and_path_query_patterns():
    from src.core.sentry import _before_breadcrumb

    crumb = {
        "category": "logging",
        "message": "transport host= internal.vendor.local diagnostic url= /v1/accounts?email=user@example.com GET /v1/orders?buyer_ref=abc",
    }

    scrubbed = _before_breadcrumb(crumb, {})

    assert scrubbed == {
        "category": "logging",
        "message": "transport host= [host] diagnostic url= [url] GET /v1/orders",
    }


def test_before_breadcrumb_scrubs_bare_relative_path_query_strings():
    from src.core.sentry import _before_breadcrumb

    crumb = {
        "category": "logging",
        "message": "GET /admin/auth/callback?code=oauth-code&state=oauth-state",
    }

    scrubbed = _before_breadcrumb(crumb, {})

    assert scrubbed == {
        "category": "logging",
        "message": "GET /admin/auth/callback",
    }


def test_capture_exception_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    from src.core.sentry import capture_exception

    with patch("sentry_sdk.capture_exception") as mock_capture:
        assert capture_exception(RuntimeError("boom")) is None

    mock_capture.assert_not_called()


def test_capture_exception_sets_scoped_tags_and_extra(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")

    from src.core import sentry

    sentry._initialized = True

    scope = MagicMock()
    with (
        patch("sentry_sdk.capture_exception", return_value="event-123") as mock_capture,
        patch("sentry_sdk.push_scope") as mock_push_scope,
    ):
        mock_push_scope.return_value.__enter__.return_value = scope
        exc = RuntimeError("boom")
        event_id = sentry.capture_exception(
            exc,
            tags={"area": "tenant-management", "empty": None},
            extra={"gam_fault": {"reason": "NOT_ALLOWED"}, "missing": None},
        )

    assert event_id == "event-123"
    mock_capture.assert_called_once_with(exc)
    scope.set_tag.assert_called_once_with("area", "tenant-management")
    scope.set_extra.assert_called_once_with("gam_fault", {"reason": "NOT_ALLOWED"})


def test_capture_exception_fails_open_when_initialization_fails(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "not-a-dsn")

    from src.core import sentry

    with patch("src.core.sentry.initialize_sentry", side_effect=RuntimeError("BadDsn")) as mock_init:
        assert sentry.capture_exception(RuntimeError("boom")) is None

    mock_init.assert_called_once_with()


def test_capture_exception_returns_none_when_sdk_drops_event(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")

    from src.core import sentry

    sentry._initialized = True
    with (
        patch("sentry_sdk.capture_exception", return_value=None) as mock_capture,
        patch("sentry_sdk.push_scope") as mock_push_scope,
    ):
        mock_push_scope.return_value.__enter__.return_value = MagicMock()
        exc = RuntimeError("boom")
        assert sentry.capture_exception(exc) is None

    mock_capture.assert_called_once_with(exc)


def test_capture_message_fails_open_when_capture_raises(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")

    from src.core import sentry

    sentry._initialized = True
    with (
        patch("sentry_sdk.push_scope") as mock_push_scope,
        patch("sentry_sdk.capture_message", side_effect=RuntimeError("transport down")),
    ):
        mock_push_scope.return_value.__enter__.return_value = MagicMock()
        assert sentry.capture_message("adapter probe failed") is None


def test_capture_message_returns_none_when_sdk_drops_event(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")

    from src.core import sentry

    sentry._initialized = True
    with (
        patch("sentry_sdk.capture_message", return_value=None) as mock_capture,
        patch("sentry_sdk.push_scope") as mock_push_scope,
    ):
        mock_push_scope.return_value.__enter__.return_value = MagicMock()
        assert sentry.capture_message("adapter probe failed") is None

    mock_capture.assert_called_once_with("adapter probe failed", level="error")


def test_capture_message_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    from src.core.sentry import capture_message

    with patch("sentry_sdk.capture_message") as mock_capture:
        assert capture_message("adapter probe failed") is None

    mock_capture.assert_not_called()


def test_capture_operation_exception_adds_area_and_operation_tags(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")

    from src.core import sentry

    sentry._initialized = True

    scope = MagicMock()
    with (
        patch("sentry_sdk.capture_exception", return_value="event-456") as mock_capture,
        patch("sentry_sdk.push_scope") as mock_push_scope,
    ):
        mock_push_scope.return_value.__enter__.return_value = scope
        exc = RuntimeError("spawn failed")
        event_id = sentry.capture_operation_exception(
            exc,
            area="tenant-management",
            operation="refresh_worker_spawn_failed",
            tags={"tenant_id": "tenant_1", "empty": None},
            extra={"sync_ids": ["sync_1"], "missing": None},
        )

    assert event_id == "event-456"
    mock_capture.assert_called_once_with(exc)
    scope.set_tag.assert_any_call("area", "tenant-management")
    scope.set_tag.assert_any_call("operation", "refresh_worker_spawn_failed")
    scope.set_tag.assert_any_call("tenant_id", "tenant_1")
    scope.set_extra.assert_called_once_with("sync_ids", ["sync_1"])


def test_capture_operation_message_adds_area_and_operation_tags(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")

    from src.core import sentry

    sentry._initialized = True

    scope = MagicMock()
    with (
        patch("sentry_sdk.capture_message", return_value="event-789") as mock_capture,
        patch("sentry_sdk.push_scope") as mock_push_scope,
    ):
        mock_push_scope.return_value.__enter__.return_value = scope
        event_id = sentry.capture_operation_message(
            "adapter probe failed",
            area="tenant-management",
            operation="adapter_test_connection",
            tags={"tenant_id": "tenant_1", "adapter_type": "google_ad_manager"},
            extra={"error_code": "permission_denied"},
        )

    assert event_id == "event-789"
    mock_capture.assert_called_once_with("adapter probe failed", level="error")
    scope.set_tag.assert_any_call("area", "tenant-management")
    scope.set_tag.assert_any_call("operation", "adapter_test_connection")
    scope.set_tag.assert_any_call("tenant_id", "tenant_1")
    scope.set_tag.assert_any_call("adapter_type", "google_ad_manager")
    scope.set_extra.assert_called_once_with("error_code", "permission_denied")
