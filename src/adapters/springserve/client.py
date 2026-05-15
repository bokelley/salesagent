"""High-level SpringServe client.

Public facade composing the per-resource sub-clients (campaigns,
demand tags, creatives, supply, reporting) behind a single object so
adapter code has one client to wire up.

Construct with either email + password (canonical, auto-refreshing) or
a pre-minted token (escape hatch). See :mod:`._transport` for HTTP
details and exception classes.
"""

from __future__ import annotations

import requests

from src.adapters.springserve._campaigns import SpringServeCampaignsClient
from src.adapters.springserve._creatives import SpringServeCreativesClient
from src.adapters.springserve._demand_tags import SpringServeDemandTagsClient
from src.adapters.springserve._transport import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    SpringServeAuthError,
    SpringServeError,
    SpringServeForbiddenError,
    SpringServeNotFoundError,
    SpringServeRateLimitError,
    SpringServeServerError,
    SpringServeTransport,
    SpringServeValidationError,
)

# Backwards-compat alias for callers that catch on the API-style name.
SpringServeAPIError = SpringServeError

__all__ = [
    "DEFAULT_BASE_URL",
    "SpringServeAPIError",
    "SpringServeAuthError",
    "SpringServeClient",
    "SpringServeError",
    "SpringServeForbiddenError",
    "SpringServeNotFoundError",
    "SpringServeRateLimitError",
    "SpringServeServerError",
    "SpringServeTransport",
    "SpringServeValidationError",
]


class SpringServeClient:
    """Composed SpringServe API client.

    ``client.campaigns`` and ``client.demand_tags`` expose typed CRUD over
    the two write surfaces the adapter cares about today. Future stages
    add ``client.creatives``, ``client.supply``, and ``client.reporting``
    against the same transport.
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
        self._transport = SpringServeTransport(
            api_token=api_token,
            email=email,
            password=password,
            base_url=base_url,
            timeout=timeout,
            session=session,
        )
        self.campaigns = SpringServeCampaignsClient(self._transport)
        self.demand_tags = SpringServeDemandTagsClient(self._transport)
        self.creatives = SpringServeCreativesClient(self._transport)

    # ----- connectivity -----

    def probe(self, method: str, path: str) -> tuple[int, str]:
        """Forward a no-raise probe to the transport. Used by the adapter's
        ``check_permissions()`` method to surface per-endpoint scope state."""
        return self._transport.probe(method, path)

    @property
    def base_url(self) -> str:
        return self._transport.base_url
