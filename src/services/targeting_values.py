"""Shared custom-targeting-value cache helpers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from src.core.database.models import GAMInventory
from src.core.database.repositories.gam_sync import GAMSyncRepository


def upsert_targeting_value_row(
    repo: GAMSyncRepository,
    *,
    key_id: str,
    key_name: str,
    key_display_name: str,
    value: Any,
    sync_time: datetime,
) -> None:
    """Upsert a ``custom_targeting_value`` row from a GAM discovery object."""
    metadata = {
        "custom_targeting_key_id": str(key_id),
        "display_name": value.display_name or value.name,
        "match_type": value.match_type or "EXACT",
        "key_name": key_name,
        "key_display_name": key_display_name,
    }
    existing = repo.find_inventory_item("custom_targeting_value", value.id)
    if existing:
        existing.name = value.name
        existing.path = [key_display_name, value.display_name or value.name]
        existing.status = value.status or "ACTIVE"
        existing.inventory_metadata = metadata
        existing.last_synced = sync_time
        return

    repo.add(
        GAMInventory(
            tenant_id=repo.tenant_id,
            inventory_type="custom_targeting_value",
            inventory_id=value.id,
            name=value.name,
            path=[key_display_name, value.display_name or value.name],
            status=value.status or "ACTIVE",
            inventory_metadata=metadata,
            last_synced=sync_time,
        )
    )


def cached_targeting_values(repo: GAMSyncRepository, key_id: str, key_name: str) -> list[dict] | None:
    """Return cached custom-targeting-value rows for ``key_id``, or None on miss."""
    rows = repo.list_values_for_key(key_id)
    if not rows:
        return None

    values = []
    for row in rows:
        md = row.inventory_metadata or {}
        values.append(
            {
                "id": row.inventory_id,
                "name": row.name,
                "display_name": md.get("display_name") or row.name,
                "match_type": md.get("match_type") or "EXACT",
                "status": row.status or "ACTIVE",
                "key_id": key_id,
                "key_name": key_name,
            }
        )
    return values


def targeting_values_synced_empty(key_row: GAMInventory) -> bool:
    """Whether this key was refreshed successfully and had no GAM values."""
    return bool((key_row.inventory_metadata or {}).get("values_synced_empty"))


def targeting_value_to_response(value: Any, *, key_id: str, key_name: str) -> dict:
    """Project a GAM discovery value onto the admin-widget response shape."""
    return {
        "id": value.id,
        "name": value.name,
        "display_name": value.display_name or value.name,
        "match_type": value.match_type or "EXACT",
        "status": value.status or "ACTIVE",
        "key_id": key_id,
        "key_name": key_name,
    }


def sync_targeting_values_for_key(
    repo: GAMSyncRepository,
    *,
    key_id: str,
    key_row: GAMInventory,
    discovery: Any,
    max_values: int = 1000,
    sync_time: datetime | None = None,
) -> list[dict]:
    """Fetch one key's values from GAM, cache them, and return response dicts."""
    gam_values = discovery.discover_custom_targeting_values_for_key(key_id, max_values=max_values)
    return persist_targeting_values_for_key(
        repo,
        key_id=key_id,
        key_row=key_row,
        gam_values=gam_values,
        sync_time=sync_time,
    )


def persist_targeting_values_for_key(
    repo: GAMSyncRepository,
    *,
    key_id: str,
    key_row: GAMInventory,
    gam_values: Sequence[Any],
    sync_time: datetime | None = None,
) -> list[dict]:
    """Cache already-fetched GAM values and return response dicts."""
    key_display_name = (key_row.inventory_metadata or {}).get("display_name") or key_row.name
    synced_at = sync_time or datetime.now(UTC)
    returned_value_ids = {str(gam_value.id) for gam_value in gam_values}

    for gam_value in gam_values:
        upsert_targeting_value_row(
            repo,
            key_id=key_id,
            key_name=key_row.name,
            key_display_name=key_display_name,
            value=gam_value,
            sync_time=synced_at,
        )

    repo.delete_values_for_key_except(key_id, returned_value_ids)
    key_metadata = dict(key_row.inventory_metadata or {})
    key_metadata["values_last_synced_at"] = synced_at.isoformat()
    key_metadata["values_synced_empty"] = not gam_values
    key_row.inventory_metadata = key_metadata
    key_row.last_synced = synced_at

    return [targeting_value_to_response(value, key_id=key_id, key_name=key_row.name) for value in gam_values]


def build_gam_inventory_discovery(adapter_config: Any, tenant_id: str) -> Any:
    """Build a GAM inventory discovery client from a tenant adapter config."""
    from src.adapters.gam import GAMClientManager, build_gam_config_from_adapter
    from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery

    if not adapter_config.gam_network_code:
        raise ValueError(f"Tenant {tenant_id!r} has no GAM network code configured")

    gam_config = build_gam_config_from_adapter(adapter_config)
    client = GAMClientManager(gam_config, network_code=adapter_config.gam_network_code).get_client()
    return GAMInventoryDiscovery(client=client, tenant_id=tenant_id)
