"""Tests for the SpringServe HTTP transport.

Covers token-grant auth, token caching, refresh-on-401, and status -> exception
mapping. Uses an injected mock session so no network calls happen.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._transport import (
    SpringServeAuthError,
    SpringServeForbiddenError,
    SpringServeNotFoundError,
    SpringServeRateLimitError,
    SpringServeServerError,
    SpringServeTransport,
    SpringServeValidationError,
)
from tests.helpers.adapter_test_helpers import stub_http_response as _stub_response


class TestBearerAuth:
    def test_authorization_header_set_raw_token(self):
        """SpringServe expects the raw token in the Authorization header --
        NOT 'Bearer <token>'. This is unusual and must not regress."""
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        SpringServeTransport(api_token="tok-abc", session=session).get_json("/campaigns")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "tok-abc"
        assert "Bearer" not in headers["Authorization"]

    def test_no_credentials_rejected(self):
        with pytest.raises(ValueError, match="api_token or .email \\+ password"):
            SpringServeTransport()

    def test_email_without_password_rejected(self):
        with pytest.raises(ValueError):
            SpringServeTransport(email="e")

    def test_password_without_email_rejected(self):
        with pytest.raises(ValueError):
            SpringServeTransport(password="p")


class TestPasswordGrant:
    """Email-grant: mint via /auth, cache, refresh on 401."""

    def _mint_response(self, token: str = "minted-tok") -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.ok = True
        mock.content = b'{"token": "...", "expiration": "..."}'
        mock.text = f'{{"token": "{token}", "expiration": "2026-01-01T12:00:00.0Z"}}'
        mock.json.return_value = {"token": token, "expiration": "2026-01-01T12:00:00.0Z"}
        return mock

    def test_first_call_mints_token_via_auth_endpoint(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("minted-1")
        session.request.return_value = _stub_response(200, content=b"[]", text="[]")

        SpringServeTransport(email="user@example.com", password="hunter2", session=session).get_json("/campaigns")

        post_call = session.post.call_args
        assert post_call.args[0].endswith("/auth")
        assert post_call.kwargs["json"] == {"email": "user@example.com", "password": "hunter2"}
        # The subsequent GET used the minted token as the bearer
        assert session.request.call_args.kwargs["headers"]["Authorization"] == "minted-1"

    def test_token_is_cached_across_calls(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("cached-tok")
        session.request.return_value = _stub_response(200, content=b"[]", text="[]")

        transport = SpringServeTransport(email="e", password="p", session=session)
        transport.get_json("/campaigns")
        transport.get_json("/demand_tags")
        transport.get_json("/supply_tags")

        assert session.post.call_count == 1
        assert session.request.call_count == 3

    def test_401_triggers_refresh_and_retry(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("fresh-tok")
        # First request 401s, second (after refresh) succeeds
        session.request.side_effect = [
            _stub_response(401, content=b"stale", text="stale"),
            _stub_response(200, content=b"[]", text="[]"),
        ]

        SpringServeTransport(email="e", password="p", session=session).get_json("/campaigns")

        # Two mints (initial + refresh after 401), two requests (original + retry)
        assert session.post.call_count == 2
        assert session.request.call_count == 2

    def test_mint_failure_raises_auth_error(self):
        session = MagicMock()
        bad_resp = MagicMock()
        bad_resp.status_code = 401
        bad_resp.ok = False
        bad_resp.content = b'{"error":"invalid"}'
        bad_resp.text = '{"error":"invalid"}'
        session.post.return_value = bad_resp

        transport = SpringServeTransport(email="e", password="wrong", session=session)
        with pytest.raises(SpringServeAuthError, match="/auth rejected"):
            transport.get_json("/campaigns")

    def test_api_token_does_not_trigger_mint(self):
        """When api_token is provided, no /auth call should ever happen,
        even on 401 -- the caller is expected to manage rotation."""
        session = MagicMock()
        session.request.return_value = _stub_response(401, content=b"stale", text="stale")
        transport = SpringServeTransport(api_token="static-tok", session=session)

        with pytest.raises(SpringServeAuthError):
            transport.get_json("/campaigns")
        assert session.post.call_count == 0
        # Only one attempt -- no refresh+retry on static-token mode
        assert session.request.call_count == 1


class TestContentNegotiation:
    def test_get_sends_accept_json(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"[]", text="[]")
        SpringServeTransport(api_token="t", session=session).get_json("/campaigns")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["Accept"] == "application/json"

    def test_post_sends_content_type_json(self):
        session = MagicMock()
        session.request.return_value = _stub_response(201, content=b'{"id":1}', text='{"id":1}')
        SpringServeTransport(api_token="t", session=session).post_json("/campaigns", {"name": "x"})

        kwargs = session.request.call_args.kwargs
        assert kwargs["method"] == "POST"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["json"] == {"name": "x"}

    def test_put_uses_put_method(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b'{"id":1}', text='{"id":1}')
        SpringServeTransport(api_token="t", session=session).put_json("/campaigns/1", {"name": "x"})

        kwargs = session.request.call_args.kwargs
        assert kwargs["method"] == "PUT"
        assert kwargs["json"] == {"name": "x"}


class TestStatusMapping:
    @pytest.mark.parametrize(
        "status,exc",
        [
            (401, SpringServeAuthError),
            (403, SpringServeForbiddenError),
            (404, SpringServeNotFoundError),
            (429, SpringServeRateLimitError),
            (400, SpringServeValidationError),
            (422, SpringServeValidationError),
            (500, SpringServeServerError),
            (503, SpringServeServerError),
        ],
    )
    def test_status_maps_to_exception(self, status, exc):
        session = MagicMock()
        session.request.return_value = _stub_response(status, text="upstream error", content=b"upstream error")
        transport = SpringServeTransport(api_token="t", session=session)

        with pytest.raises(exc) as excinfo:
            transport.get_json("/campaigns")
        assert excinfo.value.status_code == status
        assert excinfo.value.body == "upstream error"

    def test_2xx_returns_parsed_json(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b'{"x":1}', text='{"x":1}')
        session.request.return_value.json.return_value = {"x": 1}
        result = SpringServeTransport(api_token="t", session=session).get_json("/campaigns")
        assert result == {"x": 1}


class TestQueryParams:
    def test_query_string_built_from_kwargs(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"[]", text="[]")
        SpringServeTransport(api_token="t", session=session).get_json("/campaigns", page=2, per_page=50)

        url = session.request.call_args.kwargs["url"]
        assert "page=2" in url
        assert "per_page=50" in url

    def test_no_query_params_no_query_string(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"[]", text="[]")
        SpringServeTransport(api_token="t", session=session).get_json("/campaigns")

        url = session.request.call_args.kwargs["url"]
        assert "?" not in url


class TestProbe:
    """``probe()`` returns (status, body) without raising on non-2xx so
    ``check_permissions()`` can survey scope across many endpoints in one pass."""

    def test_probe_returns_status_and_body(self):
        session = MagicMock()
        session.request.return_value = _stub_response(403, content=b"forbidden", text="forbidden")
        transport = SpringServeTransport(api_token="t", session=session)

        status, body = transport.probe("GET", "/report")
        assert status == 403
        assert body == "forbidden"

    def test_probe_does_not_raise_on_4xx(self):
        session = MagicMock()
        session.request.return_value = _stub_response(404, content=b"missing", text="missing")
        transport = SpringServeTransport(api_token="t", session=session)

        # No exception even though 404 normally raises in non-probe paths
        status, _ = transport.probe("GET", "/nonexistent")
        assert status == 404
