"""HTTP client for the Triton TAP Media Buying API.

Authenticates against ``login.tritondigital.com`` (returns a short-lived JWT)
and uses that token for all calls to ``mbapi.tritondigital.com``. The JWT is
cached on the client instance and refreshed transparently when the API
responds 401.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class TritonAPIError(Exception):
    """Raised when the Triton TAP API returns an error response."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TritonClient:
    """Thin wrapper around the Triton TAP Media Buying API.

    The client manages JWT lifecycle: it logs in lazily on first call, caches
    the token, and refreshes once on 401 before re-raising.
    """

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = "https://mbapi.tritondigital.com",
        login_url: str = "https://login.tritondigital.com",
        timeout: float = 30.0,
        auth_type: str = "password",
    ):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.login_url = login_url.rstrip("/")
        self.timeout = timeout
        self.auth_type = auth_type
        self._jwt: str | None = None

    # ----- auth -----

    def login(self) -> str:
        """Exchange credentials for a JWT and cache it.

        - ``password`` (default): JSON ``{"username": ..., "password": ...}`` —
          Triton's documented user-login flow.
        - ``oauth_client_credentials``: form-encoded
          ``grant_type=client_credentials&client_id=...&client_secret=...`` per
          RFC 6749 §4.4. Standard OAuth2 token endpoints reject JSON bodies for
          this grant.
        """
        if self.auth_type == "oauth_client_credentials":
            response = requests.post(
                f"{self.login_url}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.username,
                    "client_secret": self.password,
                },
                timeout=self.timeout,
            )
        else:
            response = requests.post(
                f"{self.login_url}/oauth2/token",
                json={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
        if response.status_code != 200:
            raise TritonAPIError(
                f"Triton login failed: {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        token = response.json().get("access_token") or response.json().get("token")
        if not token:
            raise TritonAPIError(
                "Triton login response missing access_token",
                status_code=response.status_code,
                body=response.text,
            )
        self._jwt = token
        return token

    def _headers(self) -> dict[str, str]:
        if not self._jwt:
            self.login()
        return {"Authorization": f"Bearer {self._jwt}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        response = requests.request(
            method, url, headers=self._headers(), json=json, params=params, timeout=self.timeout
        )
        if response.status_code == 401:
            # JWT expired — refresh once and retry
            logger.info("Triton JWT expired, refreshing")
            self.login()
            response = requests.request(
                method, url, headers=self._headers(), json=json, params=params, timeout=self.timeout
            )
        if not response.ok:
            raise TritonAPIError(
                f"Triton {method} {path} failed: {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        return response.json() if response.content else {}

    # ----- entity operations -----

    def create_campaign(self, advertiser_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/advertisers/{advertiser_id}/campaigns", json=payload)

    def create_flight(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/campaigns/{campaign_id}/flights", json=payload)

    def update_flight(self, flight_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/flights/{flight_id}", json=payload)

    def update_campaign(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/campaigns/{campaign_id}", json=payload)

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._request("GET", f"/campaigns/{campaign_id}")

    def list_flights(self, campaign_id: str) -> list[dict[str, Any]]:
        result = self._request("GET", f"/campaigns/{campaign_id}/flights")
        return result.get("items", []) if isinstance(result, dict) else result

    def list_stations(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/stations")
        return result.get("items", []) if isinstance(result, dict) else result

    def get_publisher(self) -> dict[str, Any]:
        """Return the publisher record associated with the JWT.

        Useful as a connectivity test — if this returns 200, credentials are valid.
        """
        return self._request("GET", "/publisher")
