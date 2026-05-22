"""Shared test helpers for targeting-value cache setup."""

from __future__ import annotations

from typing import Any


def create_custom_targeting_key_row(tenant: Any, key_id: str = "17304123"):
    """Create a custom targeting key cache row for ``tenant``."""
    from tests.factories import GAMInventoryFactory

    return GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_key",
        inventory_id=key_id,
        name="audience",
        inventory_metadata={"display_name": "Audience", "type": "PREDEFINED"},
    )
