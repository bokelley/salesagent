"""Salesagent-side signing infrastructure (per-buyer-agent trust model).

Each Principal optionally carries a ``brand_domain`` (operator-typed buyer
domain — the trust anchor). When set, the verifier walks
``https://<brand_domain>/.well-known/brand.json`` via
:class:`adcp.signing.BrandJsonJwksResolver`, which auto-refreshes on
cooldown + unknown-kid cascade, so JWKS rotation propagates without
operator action.

This module owns the local glue around the ``adcp.signing`` library:

* :class:`BuyerAgentJwksCache` — process-singleton cache of
  :class:`adcp.signing.BrandJsonJwksResolver` keyed on
  ``(tenant_id, principal_id)``.
* :class:`SigningVerifyMiddleware` — Starlette ASGI middleware that
  enforces per-principal signing policy + verifies signatures.
* ``replay_store`` — :class:`adcp.signing.PgReplayStore` bootstrap with
  ``pg_cron`` auto-detection + in-process timer fallback.
* :class:`VerifiedRequestState` + contextvar bridge for downstream handlers.
"""

from __future__ import annotations

from src.core.signing.buyer_agent_jwks_cache import (
    BuyerAgentJwksCache,
    brand_json_url_for,
    get_buyer_agent_jwks_cache,
)
from src.core.signing.middleware import SigningVerifyMiddleware
from src.core.signing.replay_store import (
    bootstrap_replay_store,
    get_replay_store,
)
from src.core.signing.verified_state import (
    VerifiedRequestState,
    clear_verified_state,
    get_verified_state,
    set_verified_state,
)

__all__ = [
    "BuyerAgentJwksCache",
    "SigningVerifyMiddleware",
    "VerifiedRequestState",
    "bootstrap_replay_store",
    "clear_verified_state",
    "get_buyer_agent_jwks_cache",
    "get_replay_store",
    "brand_json_url_for",
    "get_verified_state",
    "set_verified_state",
]
