"""ASGI middleware: dispatch admin paths to a WSGI Flask app.

Mounts the existing :func:`src.admin.app.create_app` Flask application
on the same Starlette binary that ``serve(transport="both")`` produces.
No nginx in the loop.

Routing decision:

* ``/mcp/*`` → MCP (claimed by ``serve(transport="both")``'s inner
  dispatcher before this middleware decides anything)
* ``/admin/*``, ``/static/*``, ``/auth/*``, ``/login``, ``/logout``,
  ``/tenant/*``, ``/api/*``, ``/test/*``, ``/health``, ``/metrics``,
  ``/debug/*``, ``/create_tenant``, ``/signup`` → Flask via
  :class:`a2wsgi.WSGIMiddleware`
* everything else (including ``/`` and ``/.well-known/agent-card.json``)
  → A2A (the inner ``serve()`` app)

Google OAuth callback at ``/auth/google/callback`` is included in the
Flask carve-out so the existing Google Cloud Console redirect URI keeps
working unchanged.
"""

from __future__ import annotations

from typing import Any

# Path prefixes the Flask admin claims. Anything under one of these
# segments dispatches to Flask; everything else falls through to A2A
# (which serves /.well-known/agent-card.json + the A2A RPC endpoint at
# root). Order doesn't matter — first matching prefix wins.
DEFAULT_FLASK_PREFIXES: tuple[str, ...] = (
    "/admin",
    "/static",
    "/auth",
    "/login",
    "/logout",
    "/tenant",
    "/api",
    "/test",
    "/health",
    "/metrics",
    "/debug",
    "/create_tenant",
    "/signup",
)


class AdminWSGIMount:
    """Path-prefix ASGI dispatcher to a single WSGI app.

    Tested-prefixes are matched as ``path == prefix`` or
    ``path.startswith(prefix + "/")`` to avoid bleeding into siblings
    (``/admin`` matches ``/admin`` and ``/admin/foo`` but never
    ``/administrators``).

    Lifespan and websocket scopes always pass through to the inner app
    — the WSGI bridge has no lifespan, and the inner Starlette owns
    the framework's startup hooks (FastMCP session manager, a2a-sdk
    stores).
    """

    def __init__(
        self,
        app: Any,
        *,
        wsgi_app: Any,
        prefixes: tuple[str, ...] = DEFAULT_FLASK_PREFIXES,
    ) -> None:
        self.app = app
        self.wsgi_app = wsgi_app
        self.prefixes = tuple(prefixes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            for prefix in self.prefixes:
                if path == prefix or path.startswith(prefix + "/"):
                    await self.wsgi_app(scope, receive, send)
                    return
        await self.app(scope, receive, send)
