"""HTTP client for the FreeWheel Publisher API.

Implements OAuth2 ``client_credentials`` against ``{base_url}/auth/token``,
caches the bearer token (7-day TTL), and refreshes automatically on 401.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class FreeWheelAPIError(Exception):
    """Raised when the FreeWheel Publisher API returns an error response."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class FreeWheelClient:
    """Thin wrapper around the FreeWheel Publisher API.

    Manages OAuth2 client_credentials lifecycle: lazy token fetch, expiry
    tracking, and one transparent refresh on 401 before re-raising.
    """

    # Refresh slightly before the documented expiry to avoid race conditions
    # with in-flight requests crossing the boundary.
    _REFRESH_LEEWAY_SECONDS = 60 * 60  # 1 hour

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        network_id: str,
        base_url: str,
        timeout: float = 30.0,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.network_id = network_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ----- auth -----

    def _fetch_token(self) -> str:
        response = requests.post(
            f"{self.base_url}/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise FreeWheelAPIError(
                f"FreeWheel auth failed: {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        body = response.json()
        token = body.get("access_token")
        if not token:
            raise FreeWheelAPIError("FreeWheel auth response missing access_token")
        # FreeWheel returns expires_in in seconds; default to 7 days if absent.
        # Clamp the refresh leeway to half the TTL so shorter-lived tokens
        # (probes, error responses, future TTL changes) don't make us refresh
        # on every call.
        expires_in = float(body.get("expires_in", 7 * 24 * 60 * 60))
        leeway = min(self._REFRESH_LEEWAY_SECONDS, expires_in / 2)
        self._token = token
        self._token_expires_at = time.time() + expires_in - leeway
        return token

    def _headers(self) -> dict[str, str]:
        if not self._token or time.time() >= self._token_expires_at:
            self._fetch_token()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        response = requests.request(
            method, url, headers=self._headers(), json=json, params=params, timeout=self.timeout
        )
        if response.status_code == 401:
            logger.info("FreeWheel token rejected, refreshing")
            self._fetch_token()
            response = requests.request(
                method, url, headers=self._headers(), json=json, params=params, timeout=self.timeout
            )
        if not response.ok:
            raise FreeWheelAPIError(
                f"FreeWheel {method} {path} failed: {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        return response.json() if response.content else {}

    # ----- network-scoped paths -----

    def _network_path(self, *parts: str) -> str:
        return f"/networks/{self.network_id}/" + "/".join(p.strip("/") for p in parts)

    # ----- entity operations (skeletal — finalised against staging creds) -----

    def get_network(self) -> dict[str, Any]:
        """Fetch the network record. Useful as a connectivity probe.

        If this returns 200 the OAuth client + network are properly provisioned.
        """
        return self._request("GET", self._network_path())

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._network_path("campaigns"), json=payload)

    def update_campaign(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", self._network_path("campaigns", campaign_id), json=payload)

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._request("GET", self._network_path("campaigns", campaign_id))

    def create_line_item(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._network_path("campaigns", campaign_id, "line-items"), json=payload)

    def update_line_item(self, line_item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", self._network_path("line-items", line_item_id), json=payload)

    def list_line_items(self, campaign_id: str) -> list[dict[str, Any]]:
        result = self._request("GET", self._network_path("campaigns", campaign_id, "line-items"))
        return result.get("items", []) if isinstance(result, dict) else result

    def list_placements(self) -> list[dict[str, Any]]:
        result = self._request("GET", self._network_path("placements"))
        return result.get("items", []) if isinstance(result, dict) else result

    def associate_creative(self, line_item_id: str, creative_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            self._network_path("line-items", line_item_id, "creative-associations"),
            json={"creativeId": creative_id},
        )
