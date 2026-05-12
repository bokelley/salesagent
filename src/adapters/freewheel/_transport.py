"""HTTP transport for the FreeWheel Publisher API.

Knows about bearer-token auth, content-type negotiation (v3 paths return XML,
v4 paths return JSON), and HTTP status -> exception mapping. Does not know
about pagination, entity shapes, or specific endpoints — those live in
:mod:`_inventory` and :mod:`_commercial`.

Authentication uses a long-lived bearer token issued by FreeWheel (or by a
publisher who has minted one on the partner's behalf). Tokens have a ~7-day
TTL but no refresh flow is exposed; expired tokens 401 and the caller must
obtain a new one. :meth:`FreeWheelTransport.token_info` provides a cheap
connectivity probe that also surfaces remaining TTL.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.freewheel.tv"
STAGING_BASE_URL = "https://api.stg.freewheel.tv"
DEFAULT_TIMEOUT = 30.0


class FreeWheelError(Exception):
    """Base exception for FreeWheel API errors.

    Carries the HTTP status code and raw response body so callers can
    inspect them without re-reading the response.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class FreeWheelAuthError(FreeWheelError):
    """401 — the bearer token is invalid, expired, or revoked."""


class FreeWheelForbiddenError(FreeWheelError):
    """403 — the bearer is valid but lacks entitlements for this resource."""


class FreeWheelNotFoundError(FreeWheelError):
    """404 — the requested resource does not exist."""


class FreeWheelValidationError(FreeWheelError):
    """4xx (other than 401/403/404) — typically a malformed request body."""


class FreeWheelServerError(FreeWheelError):
    """5xx — FreeWheel's side is unhappy."""


class FreeWheelTransport:
    """Low-level HTTP layer for the FreeWheel Publisher API."""

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        if not api_token:
            raise ValueError("api_token is required")
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()

    # ----- public methods -----

    def get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a JSON resource. Used for v4 (inventory) endpoints."""
        response = self._request("GET", path, accept="application/json", params=params or None)
        return response.json() if response.content else {}

    def get_xml(self, path: str, **params: Any) -> ET.Element:
        """GET an XML resource. Used for v3 (commercial) endpoints."""
        response = self._request("GET", path, accept="application/xml", params=params or None)
        return ET.fromstring(response.text)

    def post_xml(self, path: str, body: str) -> ET.Element:
        """POST an XML body and parse the XML response. v3 only."""
        response = self._request(
            "POST",
            path,
            accept="application/xml",
            body=body,
            content_type="application/xml",
        )
        return ET.fromstring(response.text)

    def put_xml(self, path: str, body: str) -> ET.Element:
        """PUT an XML body and parse the XML response. v3 uses PUT (not PATCH)
        for partial updates — only the fields included in the body are
        modified server-side."""
        response = self._request(
            "PUT",
            path,
            accept="application/xml",
            body=body,
            content_type="application/xml",
        )
        return ET.fromstring(response.text)

    def delete_xml(self, path: str) -> None:
        """DELETE a v3 resource. Response body (if any) is discarded."""
        self._request("DELETE", path, accept="application/xml")

    def token_info(self) -> dict[str, Any]:
        """Connectivity probe — returns ``{user_id, expires_in, created_at}``.

        A 200 here proves the bearer is valid; the ``expires_in`` field is
        useful for surfacing remaining TTL in admin UIs.
        """
        return self.get_json("/auth/token/info")

    # ----- internals -----

    def _request(
        self,
        method: str,
        path: str,
        *,
        accept: str,
        params: dict[str, Any] | None = None,
        body: str | None = None,
        content_type: str | None = None,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "accept": accept,
        }
        if content_type:
            headers["Content-Type"] = content_type
        response = self._session.request(
            method=method,
            url=url,
            headers=headers,
            data=body,
            timeout=self.timeout,
        )
        self._raise_for_status(response, method, path)
        return response

    def _raise_for_status(self, response: requests.Response, method: str, path: str) -> None:
        if response.ok:
            return
        status = response.status_code
        body = response.text
        message = f"FreeWheel {method} {path} -> HTTP {status}"
        if status == 401:
            raise FreeWheelAuthError(message, status_code=status, body=body)
        if status == 403:
            raise FreeWheelForbiddenError(message, status_code=status, body=body)
        if status == 404:
            raise FreeWheelNotFoundError(message, status_code=status, body=body)
        if 400 <= status < 500:
            raise FreeWheelValidationError(message, status_code=status, body=body)
        raise FreeWheelServerError(message, status_code=status, body=body)
