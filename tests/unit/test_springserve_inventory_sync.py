"""Tests for the SpringServe inventory taxonomy sync.

Covers row mappers, pagination, the tag<->router enrichment pass, and the
scope-error translation. Uses MagicMock for the SpringServe client + the
DB session -- end-to-end correctness against a real DB is verified via
the live integration test (see scripts/springserve_compare_wire.py for
the wire-format probes that informed this code).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._transport import SpringServeForbiddenError
from src.adapters.springserve.inventory_sync import (
    SpringServeInventorySync,
    SupplyScopeNotGranted,
)


def _supply_partner(pid: int, name: str = "Talpa") -> dict:
    return {"id": pid, "name": name, "account_id": 1730}


def _supply_router(rid: int, partner_id: int, name: str = "Router") -> dict:
    return {
        "id": rid,
        "name": name,
        "supply_partner_id": partner_id,
        "environment": "ctv",
        "account_id": 1730,
    }


def _supply_tag(tid: int, partner_id: int, name: str = "Tag") -> dict:
    return {
        "id": tid,
        "name": name,
        "supply_partner_id": partner_id,
        "format": "video",
        "rate": "0.01",
        "rate_currency": "EUR",
    }


def _key(kid: int, key: str, name: str | None = None) -> dict:
    return {
        "id": kid,
        "key": key,
        "name": name or key,
        "definition_type": "free",
        "account_id": 1730,
    }


def _value_list(vid: int, key_id: int, name: str, free_values: list[str]) -> dict:
    return {
        "id": vid,
        "name": name,
        "key_id": key_id,
        "free_values": free_values,
        "value_ids": [],
        "account_id": 1730,
    }


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._transport = MagicMock()
    return client


@pytest.fixture
def session():
    return MagicMock()


def _make_sync(mock_client, session) -> SpringServeInventorySync:
    """Build a sync with patched paginated list methods on the supply client."""
    sync = SpringServeInventorySync(client=mock_client, tenant_id="t1", session=session)
    sync._supply = MagicMock()
    return sync


class TestRowMappers:
    def test_supply_partner_row_has_no_fks(self):
        row = SpringServeInventorySync._supply_partner_row(_supply_partner(63440, "Talpa"))
        assert row["entity_type"] == "supply_partner"
        assert row["entity_id"] == "63440"
        assert row["name"] == "Talpa"
        assert row["supply_partner_id"] is None
        assert row["supply_router_id"] is None
        assert row["key_id"] is None

    def test_supply_router_row_links_to_partner(self):
        row = SpringServeInventorySync._supply_router_row(_supply_router(148010, 63440, "MPP"))
        assert row["entity_type"] == "supply_router"
        assert row["entity_id"] == "148010"
        assert row["supply_partner_id"] == "63440"
        assert row["supply_router_id"] is None
        assert row["raw_json"]["environment"] == "ctv"

    def test_supply_tag_row_has_router_when_mapped(self):
        tag = _supply_tag(945295, 63440, "KIJK CTV")
        row = SpringServeInventorySync._supply_tag_row(tag, {"945295": "148010"})
        assert row["entity_type"] == "supply_tag"
        assert row["supply_partner_id"] == "63440"
        assert row["supply_router_id"] == "148010"

    def test_supply_tag_orphan_has_no_router(self):
        """16 of 50 Talpa tags belong to no router -- orphan path must work."""
        tag = _supply_tag(999999, 63440, "Orphan")
        row = SpringServeInventorySync._supply_tag_row(tag, {})
        assert row["supply_partner_id"] == "63440"
        assert row["supply_router_id"] is None

    def test_key_row(self):
        row = SpringServeInventorySync._key_row(_key(3999, "audience_group"))
        assert row["entity_type"] == "key"
        assert row["entity_id"] == "3999"
        assert row["name"] == "audience_group"
        assert row["supply_partner_id"] is None
        assert row["key_id"] is None  # a key is not under another key

    def test_key_row_prefers_name_over_key_field(self):
        """SpringServe keys carry both ``name`` (display) and ``key`` (wire);
        prefer the display name and fall back to the wire name."""
        row = SpringServeInventorySync._key_row({"id": 3997, "key": "station_id", "name": "Audio Stations"})
        assert row["name"] == "Audio Stations"

    def test_value_list_row_links_to_key(self):
        row = SpringServeInventorySync._value_list_row(
            _value_list(2937, 3997, "Podcast MV20-59", ["1345713", "1334483"])
        )
        assert row["entity_type"] == "value_list"
        assert row["entity_id"] == "2937"
        assert row["name"] == "Podcast MV20-59"
        assert row["key_id"] == "3997"
        # Free values preserved in raw_json so the composer can intersect them.
        assert row["raw_json"]["free_values"] == ["1345713", "1334483"]


class TestRun:
    def test_full_hierarchy_upserts_all_entity_types(self, mock_client, session):
        sync = _make_sync(mock_client, session)
        sync._supply.list_supply_partners.side_effect = [[_supply_partner(63440)], []]
        sync._supply.list_supply_routers.side_effect = [[_supply_router(148010, 63440)], []]
        sync._supply.list_supply_tags.side_effect = [
            [_supply_tag(945295, 63440), _supply_tag(999999, 63440, "Orphan")],
            [],
        ]
        # Router 148010 contains tag 945295 but not the orphan.
        sync._supply.list_supply_tags_in_router.side_effect = [[_supply_tag(945295, 63440)], []]
        sync._supply.list_keys.side_effect = [[_key(3997, "station_id")], []]
        sync._supply.list_value_lists.side_effect = [
            [_value_list(2937, 3997, "Podcast MV20-59", ["1345713"])],
            [],
        ]

        # Stub the repository so we can capture the rows passed to bulk_upsert.
        captured: list[list[dict]] = []
        with _patch_repo(captured) as repo_cls:
            result = sync.run()

        assert result.succeeded is True
        assert result.counts == {
            "supply_partner": 1,
            "supply_router": 1,
            "supply_tag": 2,
            "key": 1,
            "value_list": 1,
        }
        assert result.rows_updated == 6
        # The bulk_upsert calls happened in hierarchy order.
        types_in_order = [batch[0]["entity_type"] for batch in captured if batch]
        assert types_in_order == [
            "supply_partner",
            "supply_router",
            "supply_tag",
            "key",
            "value_list",
        ]
        # The tag in router 148010 got its supply_router_id populated.
        tag_rows = next(b for b in captured if b and b[0]["entity_type"] == "supply_tag")
        in_router = next(r for r in tag_rows if r["entity_id"] == "945295")
        orphan = next(r for r in tag_rows if r["entity_id"] == "999999")
        assert in_router["supply_router_id"] == "148010"
        assert orphan["supply_router_id"] is None
        # Committed at the end (single commit; no args).
        session.commit.assert_called_once_with()
        repo_cls.assert_called_once_with(session, "t1")

    def test_403_translates_to_scope_not_granted(self, mock_client, session):
        sync = _make_sync(mock_client, session)
        sync._supply.list_supply_partners.side_effect = SpringServeForbiddenError(
            "GET /supply_partners -> HTTP 403", status_code=403, body="{}"
        )
        with pytest.raises(SupplyScopeNotGranted):
            sync.run()

    def test_router_tag_mapping_first_seen_wins_on_overlap(self, mock_client, session):
        """Live data shows no overlap, but if SpringServe ever changes, the
        first-seen mapping wins (and a warning is logged -- not asserted here
        because the structural test for that is in the production code path)."""
        sync = _make_sync(mock_client, session)
        # Two routers each claim tag 945295 -- the conflict path.
        sync._supply.list_supply_tags_in_router.side_effect = [
            [_supply_tag(945295, 63440)],
            [],
            [_supply_tag(945295, 63440)],
            [],
        ]
        mapping = sync._build_tag_to_router_map(
            [
                _supply_router(148010, 63440, "First"),
                _supply_router(15799, 63440, "Second"),
            ]
        )
        assert mapping["945295"] == "148010"  # first-seen wins


def _patch_repo(captured: list[list[dict]]):
    """Helper: stub SpringServeInventoryRepository so bulk_upsert returns len(rows)."""
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def cm():
        with patch("src.adapters.springserve.inventory_sync.SpringServeInventoryRepository") as repo_cls:
            repo = MagicMock()

            def fake_upsert(rows):
                rows_list = list(rows)
                captured.append(rows_list)
                return len(rows_list)

            repo.bulk_upsert.side_effect = fake_upsert
            repo_cls.return_value = repo
            yield repo_cls

    return cm()
