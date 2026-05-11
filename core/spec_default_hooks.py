"""Spec-mandated default hooks wired into ``serve(pre_validation_hooks=...)``.

The AdCP spec marks several fields as wire-required but instructs sellers
to default them for pre-v3 buyers (``GetProductsRequest.buying_mode``).
Other fields shifted shape between adcp 3.x and 4.4 (``format_id`` from
string to structured reference, ``assets[*].asset_type`` from inferred to
explicit). Buyers that haven't migrated send the older shape; the SDK's
typed dispatcher would reject these before our impl runs.

adcp 5.0 (#629) introduced ``serve(pre_validation_hooks=...)`` — a
per-tool ``(tool_name, raw_args) -> raw_args`` callable invoked on the
raw wire dict **before** schema + Pydantic validation. The hooks below
replace the bytes-level ``SpecDefaultsMiddleware`` we ran pre-5.0.

Each hook returns a NEW dict; mutating the input in place breaks the
framework's context-echo path (the original is captured separately).
"""

from __future__ import annotations

from typing import Any

_GET_PRODUCTS_DEFAULTS: dict[str, str] = {
    # Spec: "Sellers receiving requests from pre-v3 clients without
    # buying_mode SHOULD default to 'brief'."
    "buying_mode": "brief",
}

#: Asset key names that map to a known ``asset_type`` literal. When a buyer
#: sends ``{"image": {...}}`` without an explicit ``asset_type`` field, the
#: SDK's typed dispatcher rejects the payload because the ``AssetVariant``
#: discriminated union can't pick a branch.
_KNOWN_ASSET_TYPES = frozenset(
    {
        "image",
        "video",
        "audio",
        "vast",
        "text",
        "url",
        "html",
        "javascript",
        "webhook",
        "css",
        "daast",
        "markdown",
        "brief",
        "catalog",
    }
)

_DEFAULT_FORMAT_AGENT_URL = "https://creative.adcontextprotocol.org/"


def _infer_asset_type(key: str, value: dict[str, Any]) -> str | None:
    if "asset_type" in value:
        return None
    if key in _KNOWN_ASSET_TYPES:
        return key
    has_content = "content" in value
    has_url = "url" in value
    has_dims = "width" in value and "height" in value
    if has_content and not has_url:
        return "text"
    if has_url and has_dims:
        # Image assets require url + width + height. Only confidently infer
        # ``image`` when all three are present.
        return "image"
    if has_url:
        # ``url`` asset only requires ``url`` — safer default when the caller
        # supplied just a URL with no dimensions.
        return "url"
    return None


def _normalise_format_id(creative: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied creative with a string ``format_id`` wrapped
    as a FormatReferenceStructuredObject. adcp 4.4 made the field a
    structured ``{agent_url, id}`` reference; pre-4.4 buyers pass a bare
    string and the SDK's union discriminator rejects them.
    """
    fid = creative.get("format_id")
    if not isinstance(fid, str):
        return creative
    return {
        **creative,
        "format_id": {"agent_url": _DEFAULT_FORMAT_AGENT_URL, "id": fid},
    }


def _demote_image_without_dims(value: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied asset value with ``asset_type=image`` demoted
    to ``url`` when width/height are missing. The URL-only variant accepts
    the same payload without dim requirements; pre-4.4 buyers frequently
    declare ``asset_type='image'`` for any image-like URL without dims.
    """
    if value.get("asset_type") != "image":
        return value
    if "width" in value and "height" in value:
        return value
    if "url" not in value:
        return value
    return {**value, "asset_type": "url"}


def _backfill_creative_assets(creative: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied creative with ``asset_type`` inferred on
    every asset value, ``format_id`` normalised, and image→url demoted
    when dims are missing.
    """
    creative = _normalise_format_id(creative)
    assets = creative.get("assets")
    if not isinstance(assets, dict):
        return creative
    new_assets: dict[str, Any] = {}
    for key, value in assets.items():
        if not isinstance(value, dict):
            new_assets[key] = value
            continue
        inferred = _infer_asset_type(key, value)
        if inferred is not None:
            value = {**value, "asset_type": inferred}
        value = _demote_image_without_dims(value)
        new_assets[key] = value
    return {**creative, "assets": new_assets}


def get_products_hook(_tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Apply spec-mandated defaults to ``get_products`` requests."""
    if not isinstance(args, dict):
        return args
    out = dict(args)
    for field, default in _GET_PRODUCTS_DEFAULTS.items():
        out.setdefault(field, default)
    return out


def sync_creatives_hook(_tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Apply spec-mandated shape normalisation to ``sync_creatives`` —
    ``asset_type`` backfill, ``format_id`` structuring, image→url demote.
    """
    if not isinstance(args, dict):
        return args
    creatives = args.get("creatives")
    if not isinstance(creatives, list):
        return args
    out = dict(args)
    out["creatives"] = [_backfill_creative_assets(c) if isinstance(c, dict) else c for c in creatives]
    return out


#: Registry passed to ``serve(pre_validation_hooks=PRE_VALIDATION_HOOKS)``.
PRE_VALIDATION_HOOKS: dict[str, Any] = {
    "get_products": get_products_hook,
    "sync_creatives": sync_creatives_hook,
}
