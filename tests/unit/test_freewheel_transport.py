"""Tests for the FreeWheel HTTP transport.

Covers bearer auth, content-type negotiation (v3 XML vs v4 JSON), and
status-code -> exception mapping. Uses an injected mock session so no
network calls happen.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.freewheel._transport import (
    FreeWheelAuthError,
    FreeWheelForbiddenError,
    FreeWheelNotFoundError,
    FreeWheelServerError,
    FreeWheelTransport,
    FreeWheelValidationError,
)


def _stub_response(status_code: int, *, content: bytes = b"", text: str = "") -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.ok = 200 <= status_code < 400
    mock.content = content
    mock.text = text
    mock.json.return_value = {} if not content else None
    return mock


class TestBearerAuth:
    def test_authorization_header_set(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="tok-abc", session=session).get_json("/x")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"

    def test_empty_token_rejected(self):
        with pytest.raises(ValueError):
            FreeWheelTransport(api_token="")


class TestContentTypeNegotiation:
    def test_v3_path_sends_accept_xml(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, text="<root/>", content=b"<root/>")
        FreeWheelTransport(api_token="t", session=session).get_xml("/services/v3/advertisers")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["accept"] == "application/xml"

    def test_v4_path_sends_accept_json(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["accept"] == "application/json"

    def test_post_xml_sets_content_type(self):
        session = MagicMock()
        session.request.return_value = _stub_response(
            200, text="<campaign><id>1</id></campaign>", content=b"<campaign><id>1</id></campaign>"
        )
        FreeWheelTransport(api_token="t", session=session).post_xml(
            "/services/v3/campaign", "<campaign><name>x</name></campaign>"
        )

        headers = session.request.call_args.kwargs["headers"]
        assert headers["Content-Type"] == "application/xml"
        assert headers["accept"] == "application/xml"

    def test_put_xml_uses_put_method(self):
        session = MagicMock()
        session.request.return_value = _stub_response(
            200, text="<campaign><id>1</id></campaign>", content=b"<campaign><id>1</id></campaign>"
        )
        FreeWheelTransport(api_token="t", session=session).put_xml(
            "/services/v3/campaign/1", "<campaign><description>x</description></campaign>"
        )

        call = session.request.call_args.kwargs
        assert call["method"] == "PUT"
        assert call["headers"]["Content-Type"] == "application/xml"


class TestStatusMapping:
    @pytest.mark.parametrize(
        "status,exc",
        [
            (401, FreeWheelAuthError),
            (403, FreeWheelForbiddenError),
            (404, FreeWheelNotFoundError),
            (400, FreeWheelValidationError),
            (422, FreeWheelValidationError),
            (500, FreeWheelServerError),
            (503, FreeWheelServerError),
        ],
    )
    def test_status_maps_to_exception(self, status, exc):
        session = MagicMock()
        session.request.return_value = _stub_response(status, text="upstream error", content=b"upstream error")
        transport = FreeWheelTransport(api_token="t", session=session)

        with pytest.raises(exc) as excinfo:
            transport.get_json("/services/v4/sites")
        assert excinfo.value.status_code == status
        assert excinfo.value.body == "upstream error"

    def test_2xx_does_not_raise(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b'{"x":1}', text='{"x":1}')
        session.request.return_value.json.return_value = {"x": 1}
        result = FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites")
        assert result == {"x": 1}


class TestQueryParams:
    def test_query_string_built_from_kwargs(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites", page=2, per_page=50)

        url = session.request.call_args.kwargs["url"]
        # Order of params can vary; check both are present.
        assert "page=2" in url
        assert "per_page=50" in url

    def test_no_query_params_no_query_string(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites")

        url = session.request.call_args.kwargs["url"]
        assert "?" not in url
