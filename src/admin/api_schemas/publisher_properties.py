"""Shared publisher-property selector schemas for admin APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.core.config import get_pydantic_extra_mode

_EXTRA_MODE = get_pydantic_extra_mode()
_SELECTOR_FIELDS = ("publisher_domain", "selection_type", "property_ids", "property_tags")


def _config() -> ConfigDict:
    return ConfigDict(extra=_EXTRA_MODE)


class PublisherPropertySelector(BaseModel):
    """AdCP publisher-property selector."""

    model_config = _config()

    publisher_domain: str = Field(..., min_length=1, max_length=255)
    selection_type: Literal["all", "by_id", "by_tag"] | None = None
    property_ids: list[str] | None = None
    property_tags: list[str] | None = None

    @model_validator(mode="after")
    def _validate_selector_shape(self) -> PublisherPropertySelector:
        has_ids = bool(self.property_ids)
        has_tags = bool(self.property_tags)
        if self.selection_type is None:
            self.selection_type = "by_id" if has_ids else "by_tag" if has_tags else "all"
        if self.selection_type == "all" and (has_ids or has_tags):
            raise ValueError("selection_type='all' cannot include property_ids/property_tags")
        if self.selection_type == "all":
            return self
        if self.selection_type == "by_id" and not has_ids:
            raise ValueError("property_ids is required when selection_type='by_id'")
        if self.selection_type == "by_tag" and not has_tags:
            raise ValueError("property_tags is required when selection_type='by_tag'")
        return self


def dump_publisher_property_selectors(
    publisher_properties: list[PublisherPropertySelector],
) -> list[dict[str, Any]]:
    return [prop.model_dump(exclude_none=True, mode="json") for prop in publisher_properties]


def coerce_stored_publisher_property_selectors(
    publisher_properties: list[dict[str, Any]],
) -> list[PublisherPropertySelector]:
    """Normalize persisted selector dicts without letting extra keys 500 reads.

    Older inventory profiles could persist full publisher property records with
    fields like ``name`` and ``property_type``. The wholesale-products API only
    exposes selector fields, so stored extras are ignored on read.
    """
    selectors: list[PublisherPropertySelector] = []
    for prop in publisher_properties:
        if not isinstance(prop, Mapping):
            continue
        try:
            selector_input = _selector_input_from_mapping(prop)
            selectors.append(PublisherPropertySelector.model_validate(selector_input))
        except (ValueError, ValidationError):
            continue
    return selectors


def _selector_input_from_mapping(prop: Mapping[str, Any]) -> dict[str, Any]:
    selector = {field: prop[field] for field in _SELECTOR_FIELDS if field in prop}
    if "publisher_domain" not in selector:
        raise ValueError("stored publisher property row is missing publisher_domain")
    if "property_ids" not in selector and prop.get("property_id"):
        selector["property_ids"] = [str(prop["property_id"])]
    elif "property_tags" not in selector and prop.get("tags"):
        selector["property_tags"] = [str(tag) for tag in prop["tags"] if str(tag)]
    if selector.get("selection_type") == "all":
        if selector.get("property_ids"):
            selector["selection_type"] = "by_id"
        elif selector.get("property_tags"):
            selector["selection_type"] = "by_tag"
    if "selection_type" not in selector:
        if not selector.get("property_ids") and not selector.get("property_tags"):
            raise ValueError("stored publisher property row is not a selector")
        return selector
    return selector
