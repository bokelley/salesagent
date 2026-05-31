"""Ephemeral bearer tokens for trusted embedded buyer identity bridging."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from threading import RLock

TOKEN_PREFIX = "embedded-identity:"
_TOKEN_TTL_SECONDS = 30.0
_MAX_TOKENS = 2048
_LOCK = RLock()
_TOKENS: dict[str, tuple[float, EmbeddedIdentityToken]] = {}


@dataclass(frozen=True)
class EmbeddedIdentityToken:
    """Resolved embedded principal carried through the SDK bearer gate."""

    principal_id: str
    tenant_id: str


def issue_embedded_identity_token(principal_id: str, tenant_id: str) -> str:
    """Issue a short-lived opaque token for one embedded buyer request."""
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    expires_at = time.monotonic() + _TOKEN_TTL_SECONDS
    with _LOCK:
        _prune_expired(now=time.monotonic())
        if len(_TOKENS) >= _MAX_TOKENS:
            _TOKENS.pop(next(iter(_TOKENS)))
        _TOKENS[token] = (expires_at, EmbeddedIdentityToken(principal_id=principal_id, tenant_id=tenant_id))
    return token


def resolve_embedded_identity_token(token: str) -> EmbeddedIdentityToken | None:
    """Return the embedded identity for a live bridge token, if any."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    now = time.monotonic()
    with _LOCK:
        item = _TOKENS.pop(token, None)
        if item is None:
            return None
        expires_at, identity = item
        if expires_at <= now:
            return None
        return identity


def _prune_expired(*, now: float) -> None:
    expired = [token for token, (expires_at, _) in _TOKENS.items() if expires_at <= now]
    for token in expired:
        _TOKENS.pop(token, None)
