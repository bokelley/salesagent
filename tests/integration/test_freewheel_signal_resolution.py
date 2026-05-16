"""Freewheel signal resolution — TenantSignal → FW line-item targeting.

Parallel to ``test_tenant_signal_flow.py``'s GAM coverage, this verifies
the same ``TenantSignal`` abstraction generalizes to FreeWheel's
fundamentally different taxonomy:

  GAM kinds → audienceTargeting block + customTargeting block (separate)
  FW kinds  → viewershipProfileIds + audienceItemIds + customCriteria (flat)

Both adapters consume the same operator-authoring shape
(``{type: passthrough|composed, kind, ...}``); each translates to its
own line-item structures.
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.targeting import TargetingOverlay

from src.adapters.freewheel.targeting import build_targeting, validate_targeting
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _FwSignalEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestFwPassthroughSignals:
    def test_viewership_profile_passthrough(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_pt_t1", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_adults_25_34",
                adapter_config={
                    "type": "passthrough",
                    "kind": "freewheel_viewership_profile",
                    "profile_id": 4711,
                },
            )
            env.get_session()

            overlay = TargetingOverlay(audience_include=["audience_adults_25_34"])
            result = build_targeting(overlay, product_config={}, tenant_id="fw_pt_t1")

        assert result.get("viewershipProfileIds") == [4711]
        assert "audienceItemIds" not in result
        assert "customCriteria" not in result

    def test_audience_item_passthrough(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_pt_t2", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_premium",
                adapter_config={
                    "type": "passthrough",
                    "kind": "freewheel_audience_item",
                    "item_id": 9876,
                },
            )
            env.get_session()
            result = build_targeting(
                TargetingOverlay(audience_include=["audience_premium"]),
                product_config={},
                tenant_id="fw_pt_t2",
            )
        assert result.get("audienceItemIds") == [9876]

    def test_custom_kv_passthrough(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_pt_t3", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="content_sports",
                adapter_config={
                    "type": "passthrough",
                    "kind": "freewheel_custom_kv",
                    "key": "genre",
                    "value_id": "sports",
                },
            )
            env.get_session()
            result = build_targeting(
                TargetingOverlay(audience_include=["content_sports"]),
                product_config={},
                tenant_id="fw_pt_t3",
            )
        # FW customCriteria is OR-within-key — values are a list.
        assert result.get("customCriteria") == [{"key": "genre", "values": ["sports"]}]

    def test_legacy_kind_without_type_discriminator_works(self, integration_db):
        """Backward compat: rows without ``type`` infer ``passthrough``."""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_pt_t4", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_x",
                adapter_config={"kind": "freewheel_viewership_profile", "profile_id": 1111},
            )
            env.get_session()
            result = build_targeting(
                TargetingOverlay(audience_include=["audience_x"]),
                product_config={},
                tenant_id="fw_pt_t4",
            )
        assert result.get("viewershipProfileIds") == [1111]


class TestFwComposedSignals:
    """Composed signals: operator bundles multiple FW criteria into one signal id."""

    def test_composed_mixes_multiple_fw_kinds(self, integration_db):
        """``premium_sports_audience`` = viewership profile + audience item + custom KV."""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_co_t1", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="premium_sports_audience",
                adapter_config={
                    "type": "composed",
                    "criteria": [
                        {"kind": "freewheel_viewership_profile", "profile_id": 4711, "mode": "include"},
                        {"kind": "freewheel_audience_item", "item_id": 9876, "mode": "include"},
                        {
                            "kind": "freewheel_custom_kv",
                            "key": "genre",
                            "value_id": "sports",
                            "mode": "include",
                        },
                    ],
                },
            )
            env.get_session()
            result = build_targeting(
                TargetingOverlay(audience_include=["premium_sports_audience"]),
                product_config={},
                tenant_id="fw_co_t1",
            )

        assert result.get("viewershipProfileIds") == [4711]
        assert result.get("audienceItemIds") == [9876]
        assert result.get("customCriteria") == [{"key": "genre", "values": ["sports"]}]

    def test_composed_signal_with_product_default_custom_kv_merges(self, integration_db):
        """Signal-supplied custom_kv layers onto product_config defaults
        (signal values append; OR-within-key semantics)."""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_co_t2", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="add_news",
                adapter_config={
                    "type": "composed",
                    "criteria": [
                        {
                            "kind": "freewheel_custom_kv",
                            "key": "genre",
                            "value_id": "news",
                            "mode": "include",
                        }
                    ],
                },
            )
            env.get_session()
            product_config = {"custom_targeting": {"genre": ["sports"]}}
            result = build_targeting(
                TargetingOverlay(audience_include=["add_news"]),
                product_config=product_config,
                tenant_id="fw_co_t2",
            )
        # product default "sports" + signal "news" → OR-within-genre
        assert result.get("customCriteria") == [{"key": "genre", "values": ["sports", "news"]}]


class TestFwExclusionRejected:
    """FW has no native per-field exclusion — buyer-side or per-criterion
    exclude must fail loud, never silently drop targeting."""

    def test_audience_exclude_validation_message(self, integration_db):
        """``audience_exclude`` is rejected at validate_targeting time
        before the FW adapter even attempts the buy."""
        overlay = TargetingOverlay(audience_exclude=["any_signal_id"])
        messages = validate_targeting(overlay)
        assert any("exclusion is not supported on FreeWheel" in m for m in messages)

    def test_composed_criterion_with_exclude_mode_raises(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_excl_t1", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="composed_with_exclude",
                adapter_config={
                    "type": "composed",
                    "criteria": [
                        {"kind": "freewheel_viewership_profile", "profile_id": 1, "mode": "include"},
                        {"kind": "freewheel_audience_item", "item_id": 2, "mode": "exclude"},
                    ],
                },
            )
            env.get_session()
            with pytest.raises(ValueError, match="exclude.*not supported by FreeWheel"):
                build_targeting(
                    TargetingOverlay(audience_include=["composed_with_exclude"]),
                    product_config={},
                    tenant_id="fw_excl_t1",
                )


class TestFwSignalErrors:
    def test_unknown_signal_id_raises(self, integration_db):
        from tests.factories import TenantFactory

        with _FwSignalEnv() as env:
            TenantFactory(tenant_id="fw_err_t1", ad_server="freewheel")
            env.get_session()
            with pytest.raises(ValueError) as exc_info:
                build_targeting(
                    TargetingOverlay(audience_include=["nope_unknown"]),
                    product_config={},
                    tenant_id="fw_err_t1",
                )
        message = str(exc_info.value)
        assert "nope_unknown" in message
        assert "fw_err_t1" in message

    def test_unknown_fw_kind_raises(self, integration_db):
        """A signal authored with a GAM-shaped kind on a FW tenant fails loud."""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _FwSignalEnv() as env:
            tenant = TenantFactory(tenant_id="fw_err_t2", ad_server="freewheel")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="wrong_adapter",
                # GAM kind — invalid for FW
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
            )
            env.get_session()
            with pytest.raises(ValueError, match="Expected kinds: freewheel_"):
                build_targeting(
                    TargetingOverlay(audience_include=["wrong_adapter"]),
                    product_config={},
                    tenant_id="fw_err_t2",
                )

    def test_no_tenant_id_skips_signal_resolution(self, integration_db):
        """Callers that don't pass tenant_id get the legacy behavior
        (no signal resolution). Preserves backward compat."""
        overlay = TargetingOverlay(audience_include=["something"])
        result = build_targeting(overlay, product_config={})  # no tenant_id
        # No signal-resolved fields should appear.
        assert "viewershipProfileIds" not in result
        assert "audienceItemIds" not in result

    def test_empty_overlay_returns_no_signal_blocks(self, integration_db):
        from tests.factories import TenantFactory

        with _FwSignalEnv() as env:
            TenantFactory(tenant_id="fw_err_t4", ad_server="freewheel")
            env.get_session()
            result = build_targeting(TargetingOverlay(), product_config={}, tenant_id="fw_err_t4")
        assert "viewershipProfileIds" not in result
        assert "audienceItemIds" not in result
