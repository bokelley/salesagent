"""Run the framework's ``media_buy_seller`` storyboard against ``core/``.

The storyboard is the canonical compliance harness — passing it is the
definition of "this stack speaks AdCP correctly." First milestone (M1) only
needs the ``get_products`` step; M2 covers the full 9-step boardwalk.

Wire-up sketch::

    @pytest.fixture
    def app():
        from core.main import build_app
        return build_app()

    @pytest.mark.asyncio
    async def test_get_products_step(app, integration_db, seeded_products):
        # Run storyboard against in-process app via httpx ASGITransport
        ...

Skeleton.
"""

from __future__ import annotations
