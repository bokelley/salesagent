"""Run the framework's ``media_buy_seller`` storyboard against ``core/``.

The storyboard is the canonical compliance harness — passing it is the
definition of "this stack speaks AdCP correctly." First milestone (M1) only
needs the ``get_products`` step; M2 covers the full 9-step boardwalk.

Wire-up sketch::

    @pytest.fixture
    def app():
        from core.main import build_app
        return build_app()

    @pytest.mark.parametrize("transport", ["mcp", "a2a"])
    @pytest.mark.asyncio
    async def test_get_products_step(app, integration_db, seeded_products, transport):
        # Run storyboard against in-process app via httpx ASGITransport,
        # POSTing to /mcp or /a2a per ``transport``.
        ...

Skeleton.

Transport coverage requirement (#10): when this skeleton is wired up, every
storyboard run MUST be parametrized across ``["mcp", "a2a"]``. The framework
serves both at the same handler via ``serve(transport="both")`` in
``core/main.py``; the wire-shape difference (JSON-RPC vs MCP streamable-http)
is exactly what the legacy ``src/a2a_server/`` used to test in-process.
With that path retired, storyboards are the only check that A2A wire shape
stays correct — they MUST run in both modes.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("transport", ["mcp", "a2a"])
@pytest.mark.skip(reason="Storyboard runner wiring is M2 — see core/README.md")
def test_media_buy_seller_storyboard(transport: str) -> None:
    """Drive the media_buy_seller storyboard against the core/ stack.

    Per #10, A2A protocol coverage is owned by storyboards now that the
    legacy in-process A2A test path is gone. This test must exercise the
    same handler through both wire surfaces — failing in either mode is
    a regression.
    """
    raise NotImplementedError(f"M2: wire storyboard runner against transport={transport!r}")
