"""Regressions for the retired AAO inverse directory sync endpoint."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from flask import session


def _response_json_and_status(response):
    if isinstance(response, tuple):
        flask_response, status = response[0], response[1]
        return flask_response.get_json(), status
    return response.get_json(), response.status_code


@contextmanager
def _authorized_request_context(app):
    with app.test_request_context(method="POST"):
        session["authenticated"] = True
        session["user"] = {"email": "test@example.com", "is_super_admin": True}
        session["email"] = "test@example.com"
        session["test_user"] = "test@example.com"
        session["test_user_role"] = "super_admin"
        session["test_user_name"] = "Test Admin"
        session["test_tenant_id"] = "unused-super-admin"
        yield


@pytest.mark.requires_db
def test_directory_sync_endpoint_is_retired_without_fetching(integration_db):
    from src.admin.app import create_app
    from src.admin.blueprints.publisher_partners import sync_publisher_partners_from_directory

    app = create_app()
    with _authorized_request_context(app):
        with patch(
            "src.services.aao_lookup_service.fetch_publishers_from_directory",
            new=AsyncMock(),
            create=True,
        ) as directory_fetch:
            response = sync_publisher_partners_from_directory("any-tenant")

    body, status = _response_json_and_status(response)
    assert status == 410
    assert "retired" in body["error"]
    assert "publisher domains" in body["error"]
    directory_fetch.assert_not_awaited()
