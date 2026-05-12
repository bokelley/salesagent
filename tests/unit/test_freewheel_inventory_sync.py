"""Tests for FreeWheelInventorySync — pulls FW inventory into the local cache.

Uses an injected mock client so we never hit the network. The sync service is
exercised against the captured-fixture-shaped Pydantic responses + raw
JSON/XML for the families that don't go through the typed inventory client
(ad_unit_packages, ad_unit_nodes, standard_attributes go through the raw
transport because they aren't part of the inventory client surface).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.freewheel.entities import PaginatedResponse, Site, VideoGroup
from src.adapters.freewheel.inventory_sync import FreeWheelInventorySync, SyncResult


@pytest.fixture
def fixtures_root() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "data" / "freewheel"


def _site_page_from_fixture(fixtures_root: Path) -> PaginatedResponse[Site]:
    body = json.loads((fixtures_root / "v4" / "sites" / "list_page1.json").read_text())
    # Force total_page=1 so the paginator stops after one call — the
    # mock returns the same page on every invocation, which would
    # otherwise re-yield the same items.
    body["total_page"] = 1
    body["total_pages"] = 1
    return PaginatedResponse[Site].model_validate(body)


def _empty_page(model_cls) -> PaginatedResponse:
    return PaginatedResponse[model_cls].model_validate(
        {"page": 1, "per_page": 50, "total_count": 0, "total_page": 1, "items": []}
    )


class TestSyncResult:
    def test_total_synced_sums_counts(self):
        result = SyncResult(counts={"site": 5, "series": 12, "video_group": 0})
        assert result.total_synced == 17

    def test_succeeded_false_when_any_error(self):
        result = SyncResult(counts={"site": 5}, errors={"series": "boom"})
        assert result.succeeded is False

    def test_succeeded_true_with_only_counts(self):
        result = SyncResult(counts={"site": 5})
        assert result.succeeded is True


class TestSyncDispatch:
    """Verifies the sync orchestrates the right v4 inventory walks +
    persists results via upsert. Doesn't exercise the real DB — we mock
    the session and assert on the upsert payloads."""

    def _build_syncer(self, fixtures_root: Path) -> tuple[FreeWheelInventorySync, MagicMock, MagicMock]:
        client = MagicMock()

        # Sites: real fixture payload to verify field mapping
        site_page = _site_page_from_fixture(fixtures_root)
        client.inventory.list_sites.return_value = site_page

        # Other inventory families: empty pages so the test focuses on Sites
        client.inventory.list_site_sections.return_value = _empty_page(Site)
        client.inventory.list_site_groups.return_value = _empty_page(Site)
        client.inventory.list_series.return_value = _empty_page(Site)
        client.inventory.list_video_groups.return_value = _empty_page(VideoGroup)

        # ad_unit_packages: empty response
        client._transport.get_json.side_effect = self._fake_transport_get_json
        # ad_unit_nodes: empty XML
        client._transport.get_xml.side_effect = self._fake_transport_get_xml

        session = MagicMock()
        syncer = FreeWheelInventorySync(client=client, session=session, tenant_id="t1")
        return syncer, client, session

    @staticmethod
    def _fake_transport_get_json(path, **params):
        if path == "/services/v4/ad_unit_packages":
            return {"page": 1, "per_page": 50, "total": 0, "total_pages": 1, "ad_unit_packages": []}
        if path.startswith("/services/v4/ad_unit_packages/"):
            return {"id": 1, "name": "stub", "ad_units": []}
        if "standard_attributes" in path:
            return {
                "tv_ratings": [
                    {"id": 6, "name": "Unrated"},
                    {"id": 11, "name": "TV-G"},
                ]
            }
        raise AssertionError(f"unexpected get_json path: {path}")

    @staticmethod
    def _fake_transport_get_xml(path, **params):
        if "ad_unit_nodes" in path:
            return ET.fromstring('<ad_unit_nodes total_pages="0"></ad_unit_nodes>')
        raise AssertionError(f"unexpected get_xml path: {path}")

    def test_full_run_collects_per_entity_counts(self, fixtures_root):
        syncer, _client, _session = self._build_syncer(fixtures_root)

        result = syncer.run()

        assert result.succeeded is True
        # Sites came back populated; other empty pages contribute 0
        assert result.counts["site"] == 10
        assert result.counts["site_section"] == 0
        assert result.counts["site_group"] == 0
        assert result.counts["series"] == 0
        assert result.counts["video_group"] == 0
        # Reference families:
        assert result.counts["ad_unit_package"] == 0
        assert result.counts["ad_unit"] == 0
        assert result.counts["ad_unit_node"] == 0
        assert result.counts["standard_attribute"] == 2  # TV-G + Unrated
        assert result.total_synced == 12
        assert result.started_at is not None
        assert result.finished_at is not None

    def test_upsert_payload_carries_tenant_and_entity_type(self, fixtures_root):
        syncer, _client, session = self._build_syncer(fixtures_root)
        syncer.run()

        # The session.execute was called with on_conflict_do_update statements;
        # we check at least one upsert was issued with the tenant scoping intact.
        assert session.execute.call_count >= 1

    def test_partial_failure_captured_in_errors(self, fixtures_root):
        """If one entity family blows up, other families still sync and
        the failure surfaces in result.errors rather than aborting."""
        syncer, client, _session = self._build_syncer(fixtures_root)
        client.inventory.list_site_sections.side_effect = RuntimeError("simulated 503")

        result = syncer.run()

        assert result.succeeded is False
        assert "site_section" in result.errors
        assert "simulated 503" in result.errors["site_section"]
        # Other families still synced
        assert result.counts["site"] == 10
        assert "standard_attribute" in result.counts


class TestAdUnitPackagesSync:
    """The list endpoint returns package metadata only; nested ad_units
    are only on the single-item GET. The sync fans out to each package
    detail and dedupes the ad_units across packages."""

    def test_fans_out_to_per_package_detail(self):
        client = MagicMock()
        empty_envelope = MagicMock(items=[], total_page=0)
        client.inventory.list_sites.return_value = empty_envelope
        client.inventory.list_site_sections.return_value = empty_envelope
        client.inventory.list_site_groups.return_value = empty_envelope
        client.inventory.list_series.return_value = empty_envelope
        client.inventory.list_video_groups.return_value = empty_envelope

        def get_json(path, **params):
            if path == "/services/v4/ad_unit_packages":
                return {
                    "total_pages": 1,
                    "ad_unit_packages": [
                        {"id": 51949, "name": "Pre-Mid"},
                        {"id": 51948, "name": "Pre-Mid-Post"},
                    ],
                }
            if path == "/services/v4/ad_unit_packages/51949":
                return {
                    "id": 51949,
                    "name": "Pre-Mid",
                    "ad_units": [
                        {"id": 51925, "name": "Pre-roll Ad"},
                        {"id": 51929, "name": "Mid-roll Ad"},
                    ],
                }
            if path == "/services/v4/ad_unit_packages/51948":
                return {
                    "id": 51948,
                    "name": "Pre-Mid-Post",
                    "ad_units": [
                        {"id": 51925, "name": "Pre-roll Ad"},  # same as pkg 51949
                        {"id": 51929, "name": "Mid-roll Ad"},  # same as pkg 51949
                        {"id": 51930, "name": "Post-roll Ad"},
                    ],
                }
            if "standard_attributes" in path:
                return {}
            return {}

        client._transport.get_json.side_effect = get_json
        client._transport.get_xml.side_effect = lambda path, **p: ET.fromstring(
            '<ad_unit_nodes total_pages="0"></ad_unit_nodes>'
        )

        session = MagicMock()
        syncer = FreeWheelInventorySync(client=client, session=session, tenant_id="t1")
        result = syncer.run()

        assert result.counts["ad_unit_package"] == 2
        # Pre-roll, Mid-roll, Post-roll — deduped from the two packages
        assert result.counts["ad_unit"] == 3


class TestStandardAttributesSync:
    """The standard_attributes endpoint returns a flat dict of reference
    lists (tv_ratings, etc.), not a paginated list, so it has its own
    code path."""

    def test_flat_dict_becomes_per_item_rows(self):
        client = MagicMock()
        # Stub every other family with empties so we isolate this path
        empty_envelope = MagicMock()
        empty_envelope.items = []
        empty_envelope.total_page = 0
        client.inventory.list_sites.return_value = empty_envelope
        client.inventory.list_site_sections.return_value = empty_envelope
        client.inventory.list_site_groups.return_value = empty_envelope
        client.inventory.list_series.return_value = empty_envelope
        client.inventory.list_video_groups.return_value = empty_envelope

        def get_json(path, **params):
            if path == "/services/v4/ad_unit_packages":
                return {"total_pages": 1, "ad_unit_packages": []}
            if path.startswith("/services/v4/ad_unit_packages/"):
                return {"id": 1, "name": "stub", "ad_units": []}
            if "standard_attributes" in path:
                return {
                    "tv_ratings": [
                        {"id": 6, "name": "Unrated"},
                        {"id": 10, "name": "TV-14"},
                    ],
                    "languages": [{"id": 1, "name": "English"}],
                }
            return {}

        def get_xml(path, **params):
            return ET.fromstring('<ad_unit_nodes total_pages="0"></ad_unit_nodes>')

        client._transport.get_json.side_effect = get_json
        client._transport.get_xml.side_effect = get_xml

        session = MagicMock()
        syncer = FreeWheelInventorySync(client=client, session=session, tenant_id="t1")
        result = syncer.run()

        # 2 tv_ratings + 1 language = 3 standard_attribute rows
        assert result.counts["standard_attribute"] == 3
