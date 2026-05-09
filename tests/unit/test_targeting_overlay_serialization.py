"""Serialization contract for the renamed TargetingOverlay class (issue #264).

After the Phase 1 rename, internal/managed fields are excluded via per-field
``exclude=True`` (replacing the previous custom ``model_dump`` exclusion). These
tests pin that contract so a future refactor cannot silently leak internal
fields onto the wire.
"""

from datetime import datetime

from src.core.schemas import Targeting, TargetingOverlay


class TestRenameAndAlias:
    def test_targeting_is_alias_for_targeting_overlay(self):
        assert Targeting is TargetingOverlay
        assert TargetingOverlay.__name__ == "TargetingOverlay"


class TestPublicDumpExcludesInternalFields:
    """``model_dump()`` must produce AdCP-spec wire shape — no internal leakage."""

    def test_tenant_id_excluded(self):
        t = TargetingOverlay(geo_countries=["US"], tenant_id="t1")
        assert "tenant_id" not in t.model_dump()

    def test_timestamps_excluded(self):
        t = TargetingOverlay(
            geo_countries=["US"],
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 2),
        )
        dumped = t.model_dump()
        assert "created_at" not in dumped
        assert "updated_at" not in dumped

    def test_metadata_excluded(self):
        t = TargetingOverlay(geo_countries=["US"], metadata={"k": "v"})
        assert "metadata" not in t.model_dump()

    def test_key_value_pairs_excluded(self):
        """Managed-only field — never exposed in overlay responses."""
        t = TargetingOverlay(geo_countries=["US"], key_value_pairs={"aee_segment": "high"})
        assert "key_value_pairs" not in t.model_dump()

    def test_json_dump_also_excludes(self):
        """``model_dump_json`` must honor per-field ``exclude=True``."""
        t = TargetingOverlay(geo_countries=["US"], tenant_id="t1", key_value_pairs={"k": "v"})
        payload = t.model_dump_json()
        assert "tenant_id" not in payload
        assert "key_value_pairs" not in payload


class TestInternalDumpPreservesAllFields:
    """``model_dump_internal()`` must round-trip everything for DB storage."""

    def test_internal_dump_includes_tenant_id(self):
        t = TargetingOverlay(geo_countries=["US"], tenant_id="t1")
        assert t.model_dump_internal()["tenant_id"] == "t1"

    def test_internal_dump_includes_key_value_pairs(self):
        t = TargetingOverlay(key_value_pairs={"aee_segment": "high"})
        assert t.model_dump_internal()["key_value_pairs"] == {"aee_segment": "high"}

    def test_internal_dump_serializes_datetimes_as_iso(self):
        t = TargetingOverlay(created_at=datetime(2026, 1, 1, 12, 0, 0))
        assert t.model_dump_internal()["created_at"] == "2026-01-01T12:00:00"

    def test_internal_dump_handles_none_timestamps(self):
        t = TargetingOverlay(geo_countries=["US"])
        result = t.model_dump_internal()
        assert result["created_at"] is None
        assert result["updated_at"] is None
