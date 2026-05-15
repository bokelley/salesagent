"""HTTP transport for the SpringServe (Magnite) ad server API.

Handles email+password auth (POST /api/v0/auth returns a bearer token with
two-hour TTL), token caching, refresh-on-401, and HTTP status -> exception
mapping. Does not know about pagination, entity shapes, or specific
endpoints -- those live in the per-resource modules (`_campaigns`,
`_demand_tags`, `_creatives`, `_reporting`).

SpringServe's auth header convention is unusual: the API uses
``Authorization: <token>`` directly, NOT ``Authorization: Bearer <token>``.
See https://springserve.atlassian.net/wiki/spaces/SSD/pages/1573617663
("API - Getting Started") for the authoritative example.

Two authentication paths are supported:

1. **Email + password (canonical)**: the transport mints a token at
   ``POST /api/v0/auth`` on first use, caches it with TTL tracking, and
   refreshes on 401 or expiry. Recommended for production.

2. **Pre-minted token (escape hatch)**: pass ``api_token`` directly. Useful
   when a partner provides a token out-of-band or for tests against a
   shared sandbox. No refresh -- 401 propagates to the caller.

Exactly one of the two paths must be provided.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests

from src.adapters._token_cache import BearerTokenCache

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://console.springserve.com/api/v0"
DEFAULT_TIMEOUT = 30.0

# SpringServe tokens have a 2-hour TTL. Refresh ~5 minutes before expiry so
# no in-flight request crosses the boundary. The actual expiration is read
# from the auth response when available.
_REFRESH_LEEWAY_SECONDS = 5 * 60
_DEFAULT_TOKEN_TTL_SECONDS = 2 * 60 * 60


class SpringServeError(Exception):
    """Base exception for SpringServe API errors.

    Carries the HTTP status code and raw response body so callers can
    inspect them without re-reading the response.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SpringServeAuthError(SpringServeError):
    """401 -- the token is invalid, expired, or revoked."""


class SpringServeForbiddenError(SpringServeError):
    """403 -- the token is valid but lacks entitlements for this resource."""


class SpringServeNotFoundError(SpringServeError):
    """404 -- the requested resource does not exist."""


class SpringServeValidationError(SpringServeError):
    """4xx (other than 401/403/404) -- typically a malformed request body."""


class SpringServeServerError(SpringServeError):
    """5xx -- SpringServe's side is unhappy."""


class SpringServeRateLimitError(SpringServeError):
    """429 -- exceeded the per-account rate limit (240 req/min general,
    10 req/min for the Reporting API)."""


class SpringServeTransport:
    """Low-level HTTP layer for the SpringServe API.

    Construct with either a pre-minted ``api_token`` (escape hatch) or
    ``email`` + ``password`` (canonical email-grant). The password path
    caches and auto-refreshes; the token path does not.
    """

    def __init__(
        self,
        api_token: str | None = None,
        *,
        email: str | None = None,
        password: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        has_password_grant = bool(email) and bool(password)
        has_token = bool(api_token)
        if not has_password_grant and not has_token:
            raise ValueError("SpringServeTransport requires either api_token or (email + password)")

        self._session = session or requests.Session()
        self._email, self._password = email, password
        self.base_url, self.timeout = base_url.rstrip("/"), timeout
        self._token_cache = BearerTokenCache(
            static_token=api_token if has_token else None,
            mint_fn=self._mint_token if has_password_grant else None,
            refresh_leeway_seconds=_REFRESH_LEEWAY_SECONDS,
        )

    # ----- public methods -----

    def get_json(self, path: str, **params: Any) -> Any:
        """GET a JSON resource. SpringServe list endpoints return arrays at the
        top level; single-resource endpoints return objects. Returns the parsed
        JSON as-is (caller is responsible for knowing the shape)."""
        response = self._request("GET", path, params=params or None)
        return response.json() if response.content else None

    def post_json(self, path: str, json_body: dict[str, Any]) -> Any:
        """POST a JSON body, parse JSON response."""
        response = self._request("POST", path, json_body=json_body)
        return response.json() if response.content else None

    def put_json(self, path: str, json_body: dict[str, Any]) -> Any:
        """PUT a JSON body, parse JSON response. SpringServe uses PUT for
        full-resource updates."""
        response = self._request("PUT", path, json_body=json_body)
        return response.json() if response.content else None

    def delete_json(self, path: str) -> None:
        """DELETE a resource. Response body (if any) is discarded."""
        self._request("DELETE", path)

    def probe(self, method: str, path: str) -> tuple[int, str]:
        """Cheap permission-check probe -- return ``(status_code, body)`` without
        raising on non-2xx. Used by ``check_permissions()`` so a single 403
        on one endpoint doesn't kill the whole probe pass.

        Auth/token-mint failures still raise (the probe can't run at all
        without a valid token) -- callers should treat that as a fatal
        precondition and surface it as a transport-level error.
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._current_token(),
            "Accept": "application/json",
        }
        response = self._session.request(method=method, url=url, headers=headers, timeout=self.timeout)
        return response.status_code, (response.text[:200] if response.text else "")

    # ----- internals -----

    def _current_token(self) -> str:
        """Return a valid token, minting/refreshing if needed."""
        return self._token_cache.current()

    def _mint_token(self) -> tuple[str, float]:
        """Email-grant: POST /auth to mint a fresh token.

        Returns ``(token, ttl_seconds)`` for :class:`BearerTokenCache` to
        cache with. SpringServe returns an ISO timestamp for ``expiration``;
        we don't parse it (the 2-hour TTL is contractual and parsing wall
        clock would couple us to SpringServe's server time).
        """
        assert self._email and self._password  # enforced in __init__
        url = f"{self.base_url}/auth"
        response = self._session.post(
            url,
            json={"email": self._email, "password": self._password},
            timeout=self.timeout,
        )
        if not response.ok:
            raise SpringServeAuthError(
                f"SpringServe /auth rejected credentials: HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        body = response.json() if response.content else {}
        token = body.get("token")
        if not token:
            raise SpringServeAuthError(
                "SpringServe /auth response missing token",
                status_code=response.status_code,
                body=response.text,
            )
        logger.info("SpringServe: minted token (TTL=%ss)", _DEFAULT_TOKEN_TTL_SECONDS)
        return token, float(_DEFAULT_TOKEN_TTL_SECONDS)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> requests.Response:
        response = self._do_request(method, path, params, json_body)
        # If we're using a minted token and got a 401, the token might have
        # rolled prematurely. Try one refresh + retry before propagating.
        if response.status_code == 401 and self._token_cache.has_mint:
            logger.info("SpringServe: 401 with cached token; minting fresh and retrying")
            self._token_cache.invalidate()
            response = self._do_request(method, path, params, json_body)
        self._raise_for_status(response, method, path)
        return response

    def _do_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Authorization": self._current_token(),
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        return self._session.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body if json_body is not None else None,
            timeout=self.timeout,
        )

    def _raise_for_status(self, response: requests.Response, method: str, path: str) -> None:
        if response.ok:
            return
        status = response.status_code
        body = response.text
        message = f"SpringServe {method} {path} -> HTTP {status}"
        if status == 401:
            raise SpringServeAuthError(message, status_code=status, body=body)
        if status == 403:
            raise SpringServeForbiddenError(message, status_code=status, body=body)
        if status == 404:
            raise SpringServeNotFoundError(message, status_code=status, body=body)
        if status == 429:
            raise SpringServeRateLimitError(message, status_code=status, body=body)
        if 400 <= status < 500:
            raise SpringServeValidationError(message, status_code=status, body=body)
        raise SpringServeServerError(message, status_code=status, body=body)
