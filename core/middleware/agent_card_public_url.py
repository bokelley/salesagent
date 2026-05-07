"""Rewrite the A2A agent-card URL fields with the request's public host.

The framework's :func:`adcp.server.a2a_server._build_agent_card` hardcodes
``http://localhost:{port}/`` at server-init time (see
``a2a_server.py:635``). It exposes no hook for injecting a public URL.

In production the bound socket is internal — the public URL comes from
the load balancer's ``X-Forwarded-Host`` (or the request ``Host``). Without
this rewrite, the static localhost URL leaks into ``/.well-known/agent-card.json``
and SDK clients that read the card to discover the JSON-RPC endpoint try
to reach ``http://localhost:8080/`` from outside the container, failing
every A2A request with ``fetch failed``. That's the entire failure mode
in #103.

This middleware intercepts the agent-card response, parses the JSON body,
rewrites the URL fields based on the inbound request's headers, and sends
the modified payload. Other responses pass through untouched.

Header precedence (request → outbound URL):

* Scheme: ``X-Forwarded-Proto`` if present, else the ASGI scope's scheme,
  else ``https`` (we never want to advertise an http URL on a TLS-fronted
  deployment by accident).
* Host: ``X-Forwarded-Host`` if present, else ``Host``.

If neither host header is present, the response passes through unchanged
— better to leak the localhost URL than render an empty/garbage one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


_AGENT_CARD_PATHS = frozenset(
    {
        "/.well-known/agent-card.json",
        "/.well-known/agent.json",  # 0.3-compat alias the SDK retains
    }
)


def _public_base_url(scope: Scope) -> str | None:
    """Derive the request's public base URL from headers, or None."""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

    forwarded_host = headers.get("x-forwarded-host")
    host = forwarded_host or headers.get("host")
    if not host:
        return None

    # Take the first value when a comma-separated list (some proxies chain).
    host = host.split(",", 1)[0].strip()
    if not host:
        return None

    forwarded_proto = headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scope_scheme = scope.get("scheme") or ""
    # Prefer X-Forwarded-Proto. Fall back to scope.scheme (set by uvicorn
    # from the listener type). Default to https — production deployments
    # always sit behind TLS, and emitting http on TLS would break SDK
    # clients that only follow https links.
    scheme = forwarded_proto or scope_scheme or "https"

    return f"{scheme}://{host}"


def _rewrite_agent_card(payload: dict[str, Any], public_base_url: str) -> dict[str, Any]:
    """Replace localhost URL fields with the public base URL.

    The card has two places to rewrite:
    * ``url`` (top-level, populated by the v0.3 compat back-fill from
      ``supportedInterfaces[0]``).
    * ``supportedInterfaces[].url`` (every entry).

    We replace only when the value resolves to a localhost loopback host
    so explicit configurations (a tenant pointing at a real public URL)
    pass through untouched.
    """
    desired = public_base_url.rstrip("/") + "/"

    if _is_loopback_url(payload.get("url")):
        payload["url"] = desired

    interfaces = payload.get("supportedInterfaces")
    if isinstance(interfaces, list):
        for iface in interfaces:
            if isinstance(iface, dict) and _is_loopback_url(iface.get("url")):
                iface["url"] = desired

    return payload


def _is_loopback_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return "://localhost" in value or "://127.0.0.1" in value or "://0.0.0.0" in value


class AgentCardPublicUrlMiddleware:
    """ASGI middleware that rewrites localhost URLs in the agent card response.

    Only acts on GET requests to ``/.well-known/agent-card.json`` (and the
    0.3 alias). Other requests pass through with no buffering overhead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in _AGENT_CARD_PATHS:
            await self.app(scope, receive, send)
            return

        public_base_url = _public_base_url(scope)
        if public_base_url is None:
            # Nothing useful to swap in — let the framework's response stand.
            await self.app(scope, receive, send)
            return

        # Buffer the response so we can rewrite the JSON body, then forward
        # a single modified ``http.response.body`` with the corrected
        # ``content-length`` header.
        start_message: Message | None = None
        body_chunks: list[bytes] = []

        async def capturing_send(message: Message) -> None:
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = message
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    body_chunks.append(message["body"])
                if not message.get("more_body", False):
                    await self._flush(send, start_message, b"".join(body_chunks), public_base_url)
            else:
                # Other message types (e.g. trailers): forward verbatim.
                await send(message)

        await self.app(scope, receive, capturing_send)

    @staticmethod
    async def _flush(
        send: Send,
        start_message: Message | None,
        body: bytes,
        public_base_url: str,
    ) -> None:
        if start_message is None:
            return

        rewritten = _try_rewrite_body(body, public_base_url)
        if rewritten is None:
            # Couldn't parse — pass the original response through unchanged.
            await send(start_message)
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        # Update content-length to match the rewritten body length.
        headers = [
            (name, value) for (name, value) in start_message.get("headers", []) if name.lower() != b"content-length"
        ]
        headers.append((b"content-length", str(len(rewritten)).encode("latin-1")))
        modified_start: Message = {
            **start_message,
            "headers": headers,
        }
        await send(modified_start)
        await send({"type": "http.response.body", "body": rewritten, "more_body": False})


def _try_rewrite_body(body: bytes, public_base_url: str) -> bytes | None:
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    rewritten = _rewrite_agent_card(payload, public_base_url)
    try:
        return json.dumps(rewritten).encode("utf-8")
    except (TypeError, ValueError):
        logger.warning("AgentCardPublicUrlMiddleware: failed to re-encode rewritten card — passing original through")
        return None
