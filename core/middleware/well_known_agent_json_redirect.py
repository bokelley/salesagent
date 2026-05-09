"""Redirect ``/.well-known/agent.json`` to ``/.well-known/agent-card.json``.

The AdCP framework's :func:`adcp.server.serve` registers a route handler
for ``/.well-known/agent-card.json`` only — the 0.3-era alias
``/.well-known/agent.json`` has no handler, so requests fall through to
nothing and Fly's edge cuts the connection with HTTP 503 (#267).

The 6.x ``@adcp/sdk`` still probes ``/agent.json`` for A2A transport
auto-detection. A 308 (permanent) redirect to ``/agent-card.json``
preserves the request method (vs 301/302 which historically rewrite POST
to GET) and tells the SDK that the alias is canonical-but-deprecated.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

_LEGACY_AGENT_CARD_PATH = "/.well-known/agent.json"
_CANONICAL_AGENT_CARD_PATH = "/.well-known/agent-card.json"


class WellKnownAgentJsonRedirectMiddleware:
    """ASGI middleware that 308-redirects the 0.3 agent-card alias.

    Sits in front of the framework's route mount so the redirect fires
    before the framework's "no such route" handler returns nothing. Other
    requests pass through with no overhead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != _LEGACY_AGENT_CARD_PATH:
            await self.app(scope, receive, send)
            return

        location = _CANONICAL_AGENT_CARD_PATH.encode("latin-1")
        await send(
            {
                "type": "http.response.start",
                "status": 308,
                "headers": [
                    (b"location", location),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})
