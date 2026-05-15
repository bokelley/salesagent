"""Shared bearer-token cache used by adapter HTTP transports.

Adapter transports (FreeWheel, SpringServe, future) all need the same
"have I got a non-expired cached token? if not, mint a fresh one" loop.
Extract it once so each transport just supplies its mint function.

This is deliberately tiny -- not a full HTTP auth abstraction. Each
adapter's transport keeps full control over how requests are constructed
(header shape, content-type, retry semantics); the only thing this module
owns is the cache+refresh dance over the token string.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class BearerTokenCache:
    """Caches a single bearer token with an absolute expiration timestamp.

    Modes:

    * **Static token** -- constructed with ``static_token=...`` and no
      ``mint_fn``. ``current()`` always returns the static value; callers
      handle rotation externally.
    * **Mint-on-demand** -- constructed with a ``mint_fn`` that returns
      ``(token, ttl_seconds)``. ``current()`` mints on first use, caches
      with ``ttl_seconds - refresh_leeway_seconds`` headroom, and re-mints
      after expiry.
    * **invalidate()** -- forget the cached token. Useful when a 401
      hits the wire and the caller wants to re-mint and retry.
    """

    def __init__(
        self,
        *,
        static_token: str | None = None,
        mint_fn: Callable[[], tuple[str, float]] | None = None,
        refresh_leeway_seconds: float = 5 * 60,
    ):
        if not static_token and not mint_fn:
            raise ValueError("BearerTokenCache requires either static_token or mint_fn")
        self._static_token = static_token
        self._mint_fn = mint_fn
        self._refresh_leeway_seconds = refresh_leeway_seconds
        self._cached_token: str | None = None
        self._cached_token_expires_at: float = 0.0

    @property
    def has_mint(self) -> bool:
        """Whether this cache can mint fresh tokens (vs. holding a static one).

        Transports use this to decide whether a 401 should trigger a refresh
        retry (mint-on-demand) or propagate immediately (static token).
        """
        return self._mint_fn is not None

    def current(self) -> str:
        """Return a valid bearer, minting/refreshing if needed."""
        if self._static_token is not None:
            return self._static_token
        if self._cached_token and time.time() < self._cached_token_expires_at:
            return self._cached_token
        assert self._mint_fn is not None  # enforced in __init__
        token, ttl = self._mint_fn()
        leeway = min(self._refresh_leeway_seconds, ttl / 2)
        self._cached_token = token
        self._cached_token_expires_at = time.time() + ttl - leeway
        return token

    def invalidate(self) -> None:
        """Forget the cached token so the next ``current()`` call re-mints."""
        self._cached_token = None
        self._cached_token_expires_at = 0.0
