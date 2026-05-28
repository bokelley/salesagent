"""Backfill ``asset_type`` discriminator on CreativeAsset construction.

adcp 4.4 made ``asset_type`` a required discriminator on every entry inside
``CreativeAsset.assets``. The wire shape change is an ergonomic regression
for pre-v3 callers and the existing test fixtures that pass shape-implicit
dicts like ``{"banner": {"url": "..."}}`` or ``{"image": {"content": "..."}}``.

We already infer ``asset_type`` inside ``_sync_creatives_impl`` for inbound
sync payloads. Callers that construct ``CreativeAsset`` directly (most of
the test surface, plus a few internal flows) bypass that path and hit the
strict library validator unchanged.

Wrapping ``CreativeAsset.__init__`` at import time applies the same inference
on every construction so the whole codebase agrees on one normalisation
rule. The wrapper is a no-op when ``asset_type`` is already present.

Importing this module installs the patch as a side-effect; the import site
should be at a load-bearing point (``src.core.schemas.__init__``) so the
patch is in place before any test or production code constructs
``CreativeAsset``.
"""

from __future__ import annotations

from typing import Any

from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from pydantic import BaseModel

# Inline copy of the inference rule (not imported from
# ``src.core.tools.creatives._sync``) to avoid a circular import — schemas
# are the deepest leaf of the import graph; tools/* depends on schemas, not
# the other way around.
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


def infer_asset_types(assets: dict[str, Any]) -> dict[str, Any]:
    """Backfill and coerce ``asset_type`` discriminators on raw asset values.

    Mirrors the SDK's pre-4.4 ``sync_creatives`` spec-compat hook; kept as a
    public helper here so production sync code, local schema validators, and
    the ``CreativeAsset.__init__`` patch share one inference rule.
    """
    coerced_assets: dict[str, Any] = {}
    for key, value in assets.items():
        if not isinstance(value, dict):
            coerced_assets[key] = value
            continue
        coerced_assets[key] = _coerce_asset(key, value)
    return coerced_assets


def _coerce_asset(asset_key: str, asset: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(asset)
    if "asset_type" not in coerced:
        if asset_key in _KNOWN_ASSET_TYPES:
            coerced["asset_type"] = asset_key
        else:
            has_content = "content" in coerced
            has_url = "url" in coerced
            has_dims = "width" in coerced and "height" in coerced
            if has_content and not has_url:
                coerced["asset_type"] = "text"
            elif has_url and has_dims:
                # Image assets require url + width + height in adcp 4.4. When
                # the caller supplies all three we can confidently infer image.
                coerced["asset_type"] = "image"
            elif has_url:
                # ``url`` asset is the safe default when only a URL is supplied
                # — only ``url`` is required, no width/height needed.
                coerced["asset_type"] = "url"
    if coerced.get("asset_type") == "image" and not ("width" in coerced and "height" in coerced):
        # SDK spec-compat demotes image-like URL assets without dimensions to
        # URL assets because the image schema requires dimensions.
        if "url" in coerced:
            coerced.pop("width", None)
            coerced.pop("height", None)
            coerced["asset_type"] = "url"
    return coerced


def normalize_assets_for_wire(assets: dict[str, Any]) -> dict[str, Any]:
    """Serialize asset values to the JSON shape validated by the SDK schema.

    Pydantic asset variants include many optional fields as ``None`` by
    default. The generated JSON schemas model those fields as non-nullable,
    so keeping ``url_type: null`` or ``description: null`` makes an otherwise
    valid asset fail the AssetVariant ``oneOf``. Normalize once for both DB
    storage and listing responses.
    """
    return infer_asset_types(_strip_none(_model_dump_assets(assets)))


def _model_dump_assets(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {key: _model_dump_assets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_model_dump_assets(item) for item in value]
    return value


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


def _apply_patch() -> None:
    if getattr(CreativeAsset, "_asset_type_compat_applied", False):
        return

    original_init = CreativeAsset.__init__

    def patched_init(self, **data: Any) -> None:
        assets = data.get("assets")
        if isinstance(assets, dict):
            data["assets"] = infer_asset_types(assets)
        original_init(self, **data)

    CreativeAsset.__init__ = patched_init  # type: ignore[method-assign,assignment]
    CreativeAsset._asset_type_compat_applied = True  # type: ignore[attr-defined]


_apply_patch()
