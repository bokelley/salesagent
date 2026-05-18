"""Signals page wiring for the SpringServe adapter.

Covers the row-loader transform from cached SpringServe inventory into the
template's row envelope, the ``springserve_value_list`` branch on the
bulk-create POST handler, and the corresponding badge index on
``TenantSignalRepository.mapped_index()``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from src.admin.blueprints.tenant_signals import _load_springserve_signal_rows


def _stub_inventory_row(
    *,
    entity_type: str,
    entity_id: str,
    name: str,
    raw_json: dict,
    key_id: str | None = None,
) -> MagicMock:
    """Mock an ORM ``SpringServeInventory`` row with just the attrs the
    loader touches."""
    row = MagicMock()
    row.entity_type = entity_type
    row.entity_id = entity_id
    row.name = name
    row.raw_json = raw_json
    row.key_id = key_id
    row.supply_partner_id = None
    row.supply_router_id = None
    return row


def _patched_repo(rows_by_type: dict[str, list]) -> MagicMock:
    """A mock SpringServeInventoryRepository where ``list_by_type(t)``
    returns the pre-built rows for type ``t``."""
    repo = MagicMock()
    repo.list_by_type.side_effect = lambda et, **_: rows_by_type.get(et, [])
    return repo


class TestLoadSpringServeSignalRows:
    def test_value_list_rows_become_audience_segments(self, monkeypatch):
        """Each value_list slot in the Audience segments card with its
        parent key namespace as the type badge."""
        key = _stub_inventory_row(
            entity_type="key",
            entity_id="3997",
            name="station_id",
            raw_json={"key": "station_id", "definition_type": "free"},
        )
        vl1 = _stub_inventory_row(
            entity_type="value_list",
            entity_id="2937",
            name="Podcast MV20-59",
            raw_json={"free_values": ["1345713", "1334483", "1336263"], "value_ids": []},
            key_id="3997",
        )
        vl2 = _stub_inventory_row(
            entity_type="value_list",
            entity_id="2949",
            name="Digital Audio MV35-54",
            raw_json={"free_values": ["1286303", "122513"], "value_ids": []},
            key_id="3997",
        )
        repo = _patched_repo({"key": [key], "value_list": [vl1, vl2]})
        monkeypatch.setattr(
            "src.admin.blueprints.tenant_signals.SpringServeInventoryRepository",
            lambda *_a, **_kw: repo,
        )

        segments, keys = _load_springserve_signal_rows(
            session=MagicMock(), tenant_id="t1", segment_index={}, mapped_payload=lambda s: None
        )

        assert [s["id"] for s in segments] == ["2937", "2949"]
        assert segments[0]["name"] == "Podcast MV20-59"
        # Parent key wire-name shows up as the type badge.
        assert segments[0]["type"] == "station_id"
        # Reach = count of values inside the list.
        assert segments[0]["reach"] == 3
        assert segments[1]["reach"] == 2
        # SpringServe-specific identifiers ride along on the row so the
        # template can stamp them onto data-attrs for the JS POST.
        assert segments[0]["_springserve_key_id"] == "3997"
        assert segments[0]["_springserve_key_name"] == "station_id"
        # Not mapped to a TenantSignal yet.
        assert segments[0]["mapped"] is None

    def test_value_list_already_mapped_carries_mapped_payload(self, monkeypatch):
        key = _stub_inventory_row(
            entity_type="key",
            entity_id="3997",
            name="station_id",
            raw_json={"key": "station_id", "definition_type": "free"},
        )
        vl = _stub_inventory_row(
            entity_type="value_list",
            entity_id="2937",
            name="Podcast MV20-59",
            raw_json={"free_values": ["x"], "value_ids": []},
            key_id="3997",
        )
        repo = _patched_repo({"key": [key], "value_list": [vl]})
        monkeypatch.setattr(
            "src.admin.blueprints.tenant_signals.SpringServeInventoryRepository",
            lambda *_a, **_kw: repo,
        )
        mapped_signal = MagicMock(signal_id="podcast_mv20_59", name="Podcast MV20-59", tags=["audio", "premium"])

        def payload(sig):
            return {
                "signal_id": sig.signal_id,
                "name": sig.name,
                "tags": sig.tags,
                "active_buys": 3,
                "last_ref": "2026-05-18",
            }

        segments, _keys = _load_springserve_signal_rows(
            session=MagicMock(),
            tenant_id="t1",
            segment_index={"2937": mapped_signal},
            mapped_payload=payload,
        )
        assert segments[0]["mapped"]["signal_id"] == "podcast_mv20_59"
        assert segments[0]["mapped"]["active_buys"] == 3

    def test_keys_become_custom_targeting_key_rows(self, monkeypatch):
        """SpringServe keys render as free-form (or predefined) custom
        targeting keys with no value rows to tick."""
        free_key = _stub_inventory_row(
            entity_type="key",
            entity_id="3999",
            name="audience_group",
            raw_json={"key": "audience_group", "definition_type": "free"},
        )
        list_key = _stub_inventory_row(
            entity_type="key",
            entity_id="3997",
            name="station_id",
            raw_json={"key": "station_id", "definition_type": "list"},
        )
        repo = _patched_repo({"key": [free_key, list_key], "value_list": []})
        monkeypatch.setattr(
            "src.admin.blueprints.tenant_signals.SpringServeInventoryRepository",
            lambda *_a, **_kw: repo,
        )

        _segments, keys = _load_springserve_signal_rows(
            session=MagicMock(), tenant_id="t1", segment_index={}, mapped_payload=lambda s: None
        )

        assert [k["id"] for k in keys] == ["3999", "3997"]
        # Free-form keys flagged so the template can render them without
        # a per-value bulk-map UI.
        assert keys[0]["is_freeform"] is True
        assert keys[0]["type"] == "FREEFORM"
        assert keys[1]["is_freeform"] is False
        assert keys[1]["type"] == "PREDEFINED"
        # Both have empty values lists -- bulk-mapping bare keys isn't
        # supported yet (no UI affordance, no bulk-create handler).
        assert keys[0]["values"] == []
        assert keys[0]["total_values"] == 0

    def test_value_list_with_no_parent_key_falls_back_gracefully(self, monkeypatch):
        """Orphan value_list (key_id None) shouldn't crash the loader."""
        vl = _stub_inventory_row(
            entity_type="value_list",
            entity_id="9999",
            name="Orphan",
            raw_json={"free_values": [], "value_ids": []},
            key_id=None,
        )
        repo = _patched_repo({"key": [], "value_list": [vl]})
        monkeypatch.setattr(
            "src.admin.blueprints.tenant_signals.SpringServeInventoryRepository",
            lambda *_a, **_kw: repo,
        )
        segments, _keys = _load_springserve_signal_rows(
            session=MagicMock(), tenant_id="t1", segment_index={}, mapped_payload=lambda s: None
        )
        assert segments[0]["id"] == "9999"
        # No key -> fall back to a placeholder so the type badge isn't blank.
        assert segments[0]["type"] == "unknown"
        assert segments[0]["_springserve_key_id"] == ""


class TestTenantSignalMappedIndexRecognizesSpringServe:
    """The shared mapped_index() that the signals page uses to show
    'already mapped' badges must recognize springserve_value_list configs."""

    def test_springserve_value_list_signal_indexed_by_value_list_id(self):
        from src.core.database.models import TenantSignal
        from src.core.database.repositories.tenant_signal import TenantSignalRepository

        sig = TenantSignal(
            tenant_id="t1",
            signal_id="podcast_mv20_59",
            name="Podcast MV20-59",
            value_type="binary",
            adapter_config={
                "type": "passthrough",
                "kind": "springserve_value_list",
                "key_id": "3997",
                "key_name": "station_id",
                "value_list_id": "2937",
            },
            data_provider="publisher",
            targeting_dimension="audience",
            tags=[],
            categories=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        # Stub the repo's list_all() so we don't need a DB session.
        repo = TenantSignalRepository.__new__(TenantSignalRepository)
        repo._session = MagicMock()
        repo._tenant_id = "t1"
        repo.list_all = lambda: [sig]  # type: ignore[method-assign]

        segment_index, kv_index = repo.mapped_index()

        assert segment_index["2937"].signal_id == "podcast_mv20_59"
        assert kv_index == {}


class TestBulkCreateSpringServeKindRoundTrip:
    """End-to-end shape verification: a TenantSignal minted from a
    springserve_value_list bulk-create payload reads back through
    mapped_index() under its value_list_id. This is the contract that
    keeps the "already mapped" badge accurate after creation.
    """

    def test_minted_signal_reappears_in_segment_index(self):
        from src.core.database.models import TenantSignal
        from src.core.database.repositories.tenant_signal import TenantSignalRepository

        # Simulate the exact shape bulk_create persists.
        minted = TenantSignal(
            tenant_id="t1",
            signal_id="podcast_mv20_59",
            name="Podcast MV20-59",
            value_type="binary",
            adapter_config={
                "type": "passthrough",
                "kind": "springserve_value_list",
                "key_id": "3997",
                "key_name": "station_id",
                "value_list_id": "2937",
            },
            data_provider="publisher",
            targeting_dimension="audience",
            tags=[],
            categories=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        repo = TenantSignalRepository.__new__(TenantSignalRepository)
        repo._session = MagicMock()
        repo._tenant_id = "t1"
        repo.list_all = lambda: [minted]  # type: ignore[method-assign]

        segment_index, _ = repo.mapped_index()
        # The bulk-map UI's "already mapped" badge looks up by the row's
        # value_list_id — same key the bulk-create handler dedupes on.
        assert "2937" in segment_index
        assert segment_index["2937"] is minted
