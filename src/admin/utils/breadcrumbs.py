"""Embed-mode breadcrumb root resolution.

Embedded tenants are rendered inside an upstream host's chrome (Scope3
Storefront, etc.). The first crumb of the salesagent-rendered breadcrumb
trail should point back at the host's storefront homepage, not at the
salesagent's own dashboard, so navigation feels native.

The override comes from one of two sources, in precedence order:

1. ``X-Embed-Breadcrumb-Root`` header — per-request, set by the upstream
   proxy on each iframe load. Lets the host hot-swap the override without
   a tenant-management round-trip.
2. ``tenant.embed_breadcrumb_root`` column — persistent, set via the
   Tenant Management API. Acts as the default when the header is absent.

Both inputs are validated through the same :class:`EmbedBreadcrumbRoot`
Pydantic model, so a malformed header value falls through to the column
rather than 500-ing the page.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import request
from pydantic import ValidationError

from src.admin.api_schemas.tenant_management import EmbedBreadcrumbRoot

logger = logging.getLogger(__name__)

EMBED_BREADCRUMB_ROOT_HEADER = "X-Embed-Breadcrumb-Root"


def _validate(payload: Any) -> dict | None:
    """Return a serialized override dict, or None if the input is invalid."""
    if payload is None:
        return None
    try:
        return EmbedBreadcrumbRoot.model_validate(payload).model_dump()
    except ValidationError as exc:
        logger.warning("Rejecting invalid embed_breadcrumb_root payload: %s", exc)
        return None


def _read_header() -> dict | None:
    """Parse the request header, if present and valid. Header is JSON-encoded."""
    raw = request.headers.get(EMBED_BREADCRUMB_ROOT_HEADER) if request else None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring malformed %s header: %s", EMBED_BREADCRUMB_ROOT_HEADER, exc)
        return None
    return _validate(parsed)


def resolve_embed_breadcrumb_root(tenant: Any | None) -> dict | None:
    """Return the embed-mode first-crumb root: header > tenant column > None.

    The override is only meaningful when ``tenant.is_embedded`` is True —
    open-instance tenants ignore both sources even if set, since their
    crumbs already point at the salesagent's own dashboard.

    Args:
        tenant: The current ``Tenant`` ORM object (or ``None`` when no
            tenant context is bound to the request).

    Returns:
        A ``{"label": str, "url": str}`` dict, or ``None`` when no override
        is configured.
    """
    if tenant is None or not getattr(tenant, "is_embedded", False):
        return None

    header_value = _read_header()
    if header_value is not None:
        return header_value

    column_value = getattr(tenant, "embed_breadcrumb_root", None)
    return _validate(column_value)


def with_embed_root_filter(crumbs: list[dict[str, Any]] | None, root: dict | None) -> list[dict[str, Any]]:
    """Inject the embed-mode root as the first crumb, if one is set.

    Used as a Jinja filter — see :func:`src.admin.app.create_app` for the
    registration. When ``root`` is ``None`` (no override active or
    open-instance tenant), the crumbs pass through unchanged.

    With 2+ crumbs, the first crumb (typically the tenant-dashboard link)
    is replaced with the override — the salesagent's own dashboard is
    redundant when the host's storefront already serves that role.

    With exactly 1 crumb (e.g. the tenant dashboard itself, where the
    only crumb is the current page), the override is prepended so the
    host link still appears upstream of the page label. Replacing would
    erase the only context the page has.
    """
    if not crumbs:
        return []
    if root is None:
        return list(crumbs)
    head = {"label": root["label"], "url": root["url"]}
    if len(crumbs) == 1:
        return [head, *list(crumbs)]
    return [head, *list(crumbs[1:])]
