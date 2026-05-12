"""FreeWheel inventory sync — pulls taxonomy into the local cache.

Walks every readable FreeWheel inventory family and upserts rows into the
``freewheel_inventory`` table. Used by the FreeWheel adapter's product
setup UI so publishers can pick targeting from their FW inventory without
round-tripping to the FW API on every page render.

This is a publisher-internal cache. AdCP buyer-facing property discovery
goes through the AAO lookup path (``adagents.json``); the data we sync
here doesn't surface there.

What gets synced (depends on token scopes):

  - Sites              (v4)
  - Site Sections      (v4)
  - Site Groups        (v4)
  - Series             (v4)
  - Video Groups       (v4)
  - Ad Unit Packages   (v4)   — with their nested Ad Units
  - Ad Unit Nodes      (v3)   — read-only; binds placement → ad_unit
  - Standard Attributes (v4)  — TV ratings reference data

Individual Videos are NOT synced (4,613+ items on Talpa's network; query
on-demand when a product needs to drill into a specific video).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.adapters.freewheel.client import FreeWheelClient
from src.core.database.models import FreeWheelInventory

logger = logging.getLogger(__name__)


# Per-resource pull config: which v4 resource → which entity_type stored
# locally, and how we extract a parent_id (where applicable). Resources not
# listed here are handled specially in :meth:`FreeWheelInventorySync.run`
# (ad_unit_packages embeds ad_units inline; ad_unit_nodes is v3 XML;
# standard_attributes returns a flat dict, not a paginated list).
_V4_INVENTORY_RESOURCES: list[tuple[str, str]] = [
    # (entity_type, FW v4 list-method name on FreeWheelInventoryClient)
    ("site", "list_sites"),
    ("site_section", "list_site_sections"),
    ("site_group", "list_site_groups"),
    ("series", "list_series"),
    ("video_group", "list_video_groups"),
]


@dataclass
class SyncResult:
    """Per-entity-type sync outcome for telemetry and admin UI display."""

    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def total_synced(self) -> int:
        return sum(self.counts.values())

    @property
    def succeeded(self) -> bool:
        return not self.errors


class FreeWheelInventorySync:
    """Pulls FW inventory into ``freewheel_inventory`` for a single tenant.

    Construct with a configured :class:`FreeWheelClient` and a SQLAlchemy
    session, then call :meth:`run`. The sync is upsert-only on the
    natural key ``(tenant_id, entity_type, entity_id)``: rows that are
    no longer present in FW will linger until you explicitly call
    :meth:`prune` (rare — FW IDs are stable).

    Per-entity-type errors are captured in the returned :class:`SyncResult`
    rather than raising; partial-success is desirable for tenants whose
    token has uneven scope coverage across families.
    """

    def __init__(self, client: FreeWheelClient, session: Session, tenant_id: str):
        self._client = client
        self._session = session
        self._tenant_id = tenant_id

    def run(self) -> SyncResult:
        """Walk every readable family and upsert into freewheel_inventory."""
        result = SyncResult(started_at=datetime.now(UTC))

        for entity_type, list_method_name in _V4_INVENTORY_RESOURCES:
            try:
                items = self._iter_v4_paginated(list_method_name)
                count = self._upsert_v4_items(entity_type, items)
                result.counts[entity_type] = count
            except Exception as exc:  # noqa: BLE001 — partial-success policy
                logger.warning("FreeWheel sync failed for %s: %s", entity_type, exc)
                result.errors[entity_type] = str(exc)

        try:
            pkg_count, ad_unit_count = self._sync_ad_unit_packages()
            result.counts["ad_unit_package"] = pkg_count
            result.counts["ad_unit"] = ad_unit_count
        except Exception as exc:  # noqa: BLE001
            logger.warning("FreeWheel sync failed for ad_unit_package: %s", exc)
            result.errors["ad_unit_package"] = str(exc)

        try:
            result.counts["ad_unit_node"] = self._sync_ad_unit_nodes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("FreeWheel sync failed for ad_unit_node: %s", exc)
            result.errors["ad_unit_node"] = str(exc)

        try:
            result.counts["standard_attribute"] = self._sync_standard_attributes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("FreeWheel sync failed for standard_attribute: %s", exc)
            result.errors["standard_attribute"] = str(exc)

        result.finished_at = datetime.now(UTC)
        return result

    def prune(self, entity_type: str, valid_ids: set[str]) -> int:
        """Delete rows for ``entity_type`` whose ``entity_id`` isn't in
        ``valid_ids``. Returns the count deleted. Use sparingly — FW IDs
        are stable and a full re-sync usually suffices."""
        stmt = delete(FreeWheelInventory).where(
            FreeWheelInventory.tenant_id == self._tenant_id,
            FreeWheelInventory.entity_type == entity_type,
            FreeWheelInventory.entity_id.notin_(valid_ids),
        )
        result = self._session.execute(stmt)
        # mypy doesn't model rowcount on generic Result; getattr is the
        # idiomatic SQLAlchemy 2.0 escape hatch.
        return int(getattr(result, "rowcount", 0) or 0)

    # ----- per-family helpers -----

    def _iter_v4_paginated(self, list_method_name: str) -> Iterator[Any]:
        """Yield every item from a v4 inventory list endpoint, paginating.

        Generic walker for endpoints exposed on
        :class:`FreeWheelInventoryClient` as ``list_*(page, per_page)``.
        Per-call page size is 50 to keep memory bounded; large taxonomies
        (4,600+ videos) would be heavy but we don't sync those.
        """
        list_method = getattr(self._client.inventory, list_method_name)
        page = 1
        while True:
            envelope = list_method(page=page, per_page=50)
            yield from envelope.items
            if page >= envelope.total_page:
                return
            page += 1

    def _upsert_v4_items(self, entity_type: str, items: Iterable[Any]) -> int:
        """Upsert a stream of v4 entities (Pydantic models) into the cache.

        Parent linkage is left ``None`` for top-level families; we
        infer it explicitly for the families where it's known (site_section
        → site, etc.) in :meth:`_extract_parent_id`.
        """
        rows = []
        for item in items:
            row = {
                "tenant_id": self._tenant_id,
                "entity_type": entity_type,
                "entity_id": str(item.id),
                "name": item.name,
                "parent_id": self._extract_parent_id(item, entity_type),
                "raw_json": item.model_dump(mode="json"),
                "last_synced_at": datetime.now(UTC),
            }
            rows.append(row)

        if not rows:
            return 0
        self._bulk_upsert(rows)
        return len(rows)

    def _bulk_upsert(self, rows: list[dict[str, Any]]) -> None:
        """Postgres-flavoured ``ON CONFLICT DO UPDATE`` upsert."""
        stmt = pg_insert(FreeWheelInventory).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "entity_type", "entity_id"],
            set_={
                "name": stmt.excluded.name,
                "parent_id": stmt.excluded.parent_id,
                "raw_json": stmt.excluded.raw_json,
                "last_synced_at": stmt.excluded.last_synced_at,
            },
        )
        self._session.execute(stmt)

    def _extract_parent_id(self, item: Any, entity_type: str) -> str | None:
        """Best-effort parent linkage from the entity payload.

        FW response shapes don't carry an explicit parent reference at the
        top level for most inventory families (parent_site_groups etc.
        come back as separate linked endpoints), so this is conservative:
        we only populate parent_id when the entity exposes it inline.
        """
        # AdUnitNodes carry placement_id inline; everything else needs the
        # linked-rels endpoints which we don't fold into the bulk sync.
        return None

    def _sync_ad_unit_packages(self) -> tuple[int, int]:
        """Pull v4 ad_unit_packages and their nested ad_units.

        The list endpoint returns package metadata only — nested ad_units
        only appear on the single-item GET. We list packages, then fetch
        each detail to harvest its ad_units. Ad_units are deduplicated
        across packages on entity_id (the same Pre-roll Ad belongs to
        both "Pre-Mid" and "Pre-Mid-Post"); first-write wins.

        Returns ``(package_count, ad_unit_count)``.
        """
        page = 1
        pkg_rows: list[dict[str, Any]] = []
        ad_unit_dict: dict[str, dict[str, Any]] = {}
        now = datetime.now(UTC)

        while True:
            body = self._client._transport.get_json("/services/v4/ad_unit_packages", page=page, per_page=50)
            packages = body.get("ad_unit_packages") or body.get("items") or []
            if not packages:
                break
            for pkg in packages:
                pkg_id = str(pkg["id"])
                # Fetch package detail to get nested ad_units inline.
                detail = self._client._transport.get_json(f"/services/v4/ad_unit_packages/{pkg_id}")
                pkg_rows.append(
                    {
                        "tenant_id": self._tenant_id,
                        "entity_type": "ad_unit_package",
                        "entity_id": pkg_id,
                        "name": detail.get("name") or pkg.get("name"),
                        "parent_id": None,
                        "raw_json": detail,
                        "last_synced_at": now,
                    }
                )
                for au in detail.get("ad_units") or []:
                    au_id = str(au["id"])
                    ad_unit_dict.setdefault(
                        au_id,
                        {
                            "tenant_id": self._tenant_id,
                            "entity_type": "ad_unit",
                            "entity_id": au_id,
                            "name": au.get("name"),
                            "parent_id": pkg_id,
                            "raw_json": au,
                            "last_synced_at": now,
                        },
                    )
            total_pages = int(body.get("total_pages", body.get("total_page", 1)) or 1)
            if page >= total_pages:
                break
            page += 1

        if pkg_rows:
            self._bulk_upsert(pkg_rows)
        if ad_unit_dict:
            self._bulk_upsert(list(ad_unit_dict.values()))
        return len(pkg_rows), len(ad_unit_dict)

    def _sync_ad_unit_nodes(self) -> int:
        """Pull v3 ad_unit_nodes (XML) — the placement↔ad_unit binding."""
        page = 1
        rows: list[dict[str, Any]] = []
        now = datetime.now(UTC)

        while True:
            root = self._client._transport.get_xml("/services/v3/ad_unit_nodes", page=page, per_page=50)
            attrs = root.attrib
            total_pages = int(attrs.get("total_pages", "1"))
            nodes = root.findall("ad_unit_node")
            if not nodes:
                break
            for node in nodes:
                node_id_el = node.find("id")
                if node_id_el is None or node_id_el.text is None:
                    continue
                node_id = node_id_el.text
                name_el = node.find("name")
                placement_el = node.find("placement_id")
                rows.append(
                    {
                        "tenant_id": self._tenant_id,
                        "entity_type": "ad_unit_node",
                        "entity_id": node_id,
                        "name": name_el.text if name_el is not None else None,
                        "parent_id": placement_el.text if placement_el is not None else None,
                        "raw_json": _element_to_jsonable(node),
                        "last_synced_at": now,
                    }
                )
            if page >= total_pages:
                break
            page += 1

        if rows:
            self._bulk_upsert(rows)
        return len(rows)

    def _sync_standard_attributes(self) -> int:
        """Pull v4 standard_attributes (TV ratings + similar reference data)."""
        body = self._client._transport.get_json("/services/v4/standard_attributes")
        now = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        for attr_kind, items in body.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if item_id is None:
                    continue
                rows.append(
                    {
                        "tenant_id": self._tenant_id,
                        "entity_type": "standard_attribute",
                        "entity_id": f"{attr_kind}:{item_id}",
                        "name": item.get("name"),
                        "parent_id": attr_kind,
                        "raw_json": item,
                        "last_synced_at": now,
                    }
                )
        if rows:
            self._bulk_upsert(rows)
        return len(rows)


def _element_to_jsonable(elem: ET.Element) -> dict[str, Any]:
    """Convert an XML element into a JSON-serialisable dict. Mirrors the
    shape of the live FW response for any v3 entity."""
    result: dict[str, Any] = {}
    for child in elem:
        if len(child) > 0:
            result[child.tag] = _element_to_jsonable(child)
        else:
            text = (child.text or "").strip()
            result[child.tag] = text if text else None
    return result
