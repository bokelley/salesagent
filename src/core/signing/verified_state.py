"""Request-scoped contextvar bridge for verified RFC 9421 signature state.

PR 2B of [signing-non-embedded](../../../../docs/design/signing-non-embedded.md).

The :class:`SigningVerifyMiddleware` runs at the ASGI layer and produces three
fields per accepted signature: the operator id, the agent_url that signed,
and the JWK ``kid`` that matched. Downstream handlers (FastMCP middleware,
A2A handlers, ``resolve_identity_from_context``) need to read these values
without coupling to the ASGI scope shape.

We use a single :class:`contextvars.ContextVar` because the verifier
middleware and downstream identity resolvers run in the same asyncio task —
contextvars propagate naturally across that boundary, including across
``await`` and through the FastMCP middleware chain.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedRequestState:
    """Verified-signer state attached to a single buyer-protocol request.

    All three fields are populated when ``SigningVerifyMiddleware`` accepts
    a signature; the absence of the contextvar (or a ``None`` value) means
    no signature was verified on this request.
    """

    principal_id: str
    agent_url: str
    key_id: str


_verified_state: ContextVar[VerifiedRequestState | None] = ContextVar("salesagent_verified_request_state", default=None)


def set_verified_state(state: VerifiedRequestState) -> None:
    """Stash the verified state for the current request.

    Called by :class:`SigningVerifyMiddleware` after a successful verify.
    """
    _verified_state.set(state)


def get_verified_state() -> VerifiedRequestState | None:
    """Read the verified state for the current request, or ``None``."""
    return _verified_state.get()


def clear_verified_state() -> None:
    """Reset to ``None`` — primarily for tests that share an event loop."""
    _verified_state.set(None)
