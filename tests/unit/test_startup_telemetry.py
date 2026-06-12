from unittest.mock import patch

import pytest


def test_initialize_application_calls_init_telemetry():
    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration"),
        patch("src.core.startup.initialize_sentry"),
        patch("src.core.startup.init_telemetry") as mock_init_tel,
        patch("src.core.startup.instrument_sqlalchemy"),
    ):
        from src.core.startup import initialize_application

        initialize_application()
        mock_init_tel.assert_called_once_with()


def test_initialize_application_calls_instrument_sqlalchemy():
    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration"),
        patch("src.core.startup.initialize_sentry"),
        patch("src.core.startup.init_telemetry"),
        patch("src.core.startup.instrument_sqlalchemy") as mock_instrument,
    ):
        from src.core.startup import initialize_application

        initialize_application()
        mock_instrument.assert_called_once_with()


def test_initialize_application_captures_and_flushes_startup_failure():
    failure = RuntimeError("bad config")
    try:
        raise failure
    except RuntimeError as exc:
        failure = exc

    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration", side_effect=failure),
        patch("src.core.startup.initialize_sentry"),
        patch("src.core.startup.init_telemetry"),
        patch("src.core.startup.instrument_sqlalchemy"),
        patch("src.core.startup.capture_sentry_exception") as mock_capture,
        patch("src.core.startup.flush_sentry") as mock_flush,
    ):
        from src.core.startup import initialize_application

        with pytest.raises(SystemExit):
            initialize_application()

    mock_capture.assert_called_once_with(
        failure,
        tags={"area": "startup", "operation": "initialize_application"},
        extra={"exception_type": "RuntimeError"},
    )
    mock_flush.assert_called_once_with()


def test_initialize_application_ignores_sentry_failures_during_startup_failure():
    failure = RuntimeError("bad config")

    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration", side_effect=failure),
        patch("src.core.startup.initialize_sentry"),
        patch("src.core.startup.init_telemetry"),
        patch("src.core.startup.instrument_sqlalchemy"),
        patch("src.core.startup.capture_sentry_exception", side_effect=RuntimeError("sentry capture failed")),
        patch("src.core.startup.flush_sentry", side_effect=RuntimeError("sentry flush failed")),
    ):
        from src.core.startup import initialize_application

        with pytest.raises(SystemExit):
            initialize_application()
