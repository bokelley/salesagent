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

    def test_excluded_when_nested_inside_parent(self):
        """Per-field ``exclude=True`` must fire even when the parent does the dumping.

        This is the regression that the prior custom ``model_dump`` override
        missed: it only ran when ``Targeting.model_dump()`` was called directly,
        so internal fields could leak through ``PackageRequest.model_dump()``.
        """
        from src.core.schemas import PackageRequest

        pkg = PackageRequest(
            product_id="prod_1",
            budget=1000.0,
            pricing_option_id="cpm_default",
            targeting_overlay=TargetingOverlay(
                geo_countries=["US"],
                tenant_id="t1",
                key_value_pairs={"aee_segment": "high"},
            ),
        )
        nested = pkg.model_dump()["targeting_overlay"]
        assert "tenant_id" not in nested
        assert "key_value_pairs" not in nested


class TestInternalDumpPreservesAllFields:
    """``model_dump_internal()`` must round-trip everything for DB storage."""

    def test_internal_dump_includes_tenant_id(self):
        t = TargetingOverlay(geo_countries=["US"], tenant_id="t1")
        assert t.model_dump_internal()["tenant_id"] == "t1"

    def test_internal_dump_includes_key_value_pairs(self):
        t = TargetingOverlay(key_value_pairs={"aee_segment": "high"})
        assert t.model_dump_internal()["key_value_pairs"] == {"aee_segment": "high"}

    def test_internal_dump_serializes_datetime_as_iso(self):
        """Default mode is ``json`` so callers can ``json.dumps()`` the result —
        this is the existing DB-storage contract (see test_v3_targeting_roundtrip)."""
        t = TargetingOverlay(created_at=datetime(2026, 1, 1, 12, 0, 0))
        assert t.model_dump_internal()["created_at"] == "2026-01-01T12:00:00"

    def test_internal_dump_default_keeps_none_internal_fields(self):
        """Default behavior preserves explicit ``None`` so DB writes can null them out."""
        t = TargetingOverlay(geo_countries=["US"])
        result = t.model_dump_internal()
        assert result["tenant_id"] is None
        assert result["created_at"] is None
        assert result["key_value_pairs"] is None

    def test_internal_dump_honors_exclude_none(self):
        """When the caller asks for exclude_none, internal fields skip None too —
        matches the existing JSON-roundtrip contract in test_v3_targeting_roundtrip."""
        t = TargetingOverlay(geo_countries=["US"])
        result = t.model_dump_internal(exclude_none=True)
        assert "tenant_id" not in result
        assert "created_at" not in result
        assert "key_value_pairs" not in result


class TestPersistedHydrationToleratesDroppedFields:
    """``model_validate_persisted`` is for DB hydration — must tolerate keys
    removed in past schema cleanups (e.g. #280 wave A drops)."""

    def test_dropped_fields_in_db_row_do_not_raise(self):
        """A persisted row written before a field was dropped must still hydrate."""
        legacy_row = {
            "geo_countries": ["US"],
            # All dropped in #280 wave A:
            "connection_type_any_of": [1, 2],
            "connection_type_none_of": [3],
            "os_none_of": ["android"],
            "browser_none_of": ["ie"],
            "media_type_none_of": ["display"],
            "content_cat_none_of": ["IAB1"],
            "keywords_none_of": ["spam"],
        }
        t = TargetingOverlay.model_validate_persisted(legacy_row)
        # Surviving fields hydrated correctly
        assert t.geo_countries is not None
        # Dropped fields are simply not present (no AttributeError, no validation error)
        assert not hasattr(t, "connection_type_any_of")

    def test_legacy_geo_aliases_still_normalize(self):
        """Stripping unknown keys must not strip the legacy-geo aliases the
        ``normalize_legacy_geo`` validator depends on."""
        legacy_row = {"geo_country_any_of": ["US", "CA"], "geo_metro_any_of": ["501"]}
        t = TargetingOverlay.model_validate_persisted(legacy_row)
        assert t.geo_countries is not None
        assert len(t.geo_countries) == 2
        assert t.geo_metros is not None

    def test_passthrough_when_already_typed(self):
        existing = TargetingOverlay(geo_countries=["US"])
        assert TargetingOverlay.model_validate_persisted(existing) is existing

    def test_buyer_path_still_strict(self):
        """``model_validate_persisted`` is the lenient hatch — direct construction
        and ``model_validate`` keep their strict ``extra='forbid'`` contract for
        dev/CI."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TargetingOverlay(connection_type_any_of=[1])
