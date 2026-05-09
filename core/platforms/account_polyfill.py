"""Polyfill: wire ``sync_accounts`` / ``list_accounts`` dispatch on
:class:`adcp.decisioning.handler.PlatformHandler`.

adcp 4.5.0 / 4.6.0 ship those handler methods as ``_not_supported``
stubs — the framework knows the spec defines the wire skills (their
input schemas live in :mod:`adcp.server.mcp_tools`) but never wired
the ``_invoke_platform_method`` glue that the other ~40 dispatchers
use. Result: a ``DecisioningPlatform`` that exposes
:class:`AccountStoreUpsert` / :class:`AccountStoreList` on its
``accounts`` store is invisible on the wire — every call returns
``OPERATION_NOT_SUPPORTED``.

This module rebinds ``PlatformHandler.sync_accounts`` and
:meth:`PlatformHandler.list_accounts` to forward to
``platform.accounts.upsert`` / ``.list``, then mutates
``_HANDLER_TOOLS["PlatformHandler"]`` and every ``sales-*`` entry of
``SPECIALISM_TO_ADVERTISED_TOOLS`` so the per-instance specialism
filter inside ``get_tools_for_handler`` doesn't strip the two tools
back out.

Importing this module applies the patches as a side-effect. The import
site lives in :mod:`core.main` (load-bearing) so the patch is in place
before :func:`adcp.decisioning.serve` constructs the handler.

**Removal path:** Filed upstream at
https://github.com/adcontextprotocol/adcp-client-python/pull/609.
Drop this module once we bump to the framework version that includes
that fix (>= 4.6.1 most likely). Tracked at
https://github.com/adcontextprotocol/adcp-client/issues/1631.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from adcp.decisioning.handler import PlatformHandler
from adcp.server.base import ToolContext

logger = logging.getLogger(__name__)

_ACCOUNT_TOOLS = frozenset({"sync_accounts", "list_accounts"})


async def _maybe_await(value: Any) -> Any:
    """Awaitable-aware passthrough — sync stores return values directly."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _patched_sync_accounts(
    self: PlatformHandler,
    params: Any,
    context: ToolContext | None = None,
) -> Any:
    """Dispatch ``sync_accounts`` to the platform's account store.

    The framework's :class:`AccountStoreUpsert` Protocol parks the
    handler on the platform's ``accounts`` store (not on the platform
    itself) — :class:`LazyPlatformRouter` explicitly excludes account
    methods from its per-tenant delegation in ``_ACCOUNT_STORE_METHODS``.
    Forward to ``platform.accounts.upsert(params, ctx)`` and let the
    store decide its own return shape (we accept the full
    ``SyncAccountsResponse`` so the impl can carry domain rejections /
    setup envelopes that ``list[SyncAccountsResultRow]`` can't).
    """
    tool_ctx = context or ToolContext()
    accounts_store = getattr(self._platform, "accounts", None)
    upsert = getattr(accounts_store, "upsert", None) if accounts_store is not None else None
    if upsert is None:
        return self._not_supported("sync_accounts")
    account = await self._resolve_account(None, tool_ctx)
    ctx = self._build_ctx(tool_ctx, account)
    return await _maybe_await(upsert(params, ctx=ctx))


async def _patched_list_accounts(
    self: PlatformHandler,
    params: Any,
    context: ToolContext | None = None,
) -> Any:
    """Dispatch ``list_accounts`` to the platform's account store."""
    tool_ctx = context or ToolContext()
    accounts_store = getattr(self._platform, "accounts", None)
    list_fn = getattr(accounts_store, "list", None) if accounts_store is not None else None
    if list_fn is None:
        return self._not_supported("list_accounts")
    account = await self._resolve_account(None, tool_ctx)
    ctx = self._build_ctx(tool_ctx, account)
    return await _maybe_await(list_fn(params, ctx=ctx))


def _apply_patch() -> None:
    if getattr(PlatformHandler, "_account_polyfill_applied", False):
        return

    PlatformHandler.sync_accounts = _patched_sync_accounts  # type: ignore[method-assign,assignment]
    PlatformHandler.list_accounts = _patched_list_accounts  # type: ignore[method-assign,assignment]
    PlatformHandler._account_polyfill_applied = True  # type: ignore[attr-defined]

    # Add the two tools to PlatformHandler's advertised universe.
    # ``register_handler_tools`` raises on conflicting tool sets, so
    # mutate the underlying registry directly — it's a plain set.
    from adcp.server.mcp_tools import _HANDLER_TOOLS

    _HANDLER_TOOLS.setdefault("PlatformHandler", set()).update(_ACCOUNT_TOOLS)

    # Add the two tools to every sales-* specialism's per-instance set
    # so ``advertised_tools_for_instance`` lets them through. Without
    # this, the per-instance specialism filter inside
    # ``get_tools_for_handler`` strips them back out.
    from adcp.decisioning.handler import SPECIALISM_TO_ADVERTISED_TOOLS

    for slug, tools in list(SPECIALISM_TO_ADVERTISED_TOOLS.items()):
        if slug.startswith("sales-"):
            SPECIALISM_TO_ADVERTISED_TOOLS[slug] = tools | _ACCOUNT_TOOLS

    logger.info("account_polyfill: rebound sync_accounts/list_accounts on PlatformHandler")


_apply_patch()
