"""End-to-end signal flow: operator authoring → get_signals discovery → GAM resolution.

Walks the full vertical added in the composition branch:

  1. Operator declares a ``TenantSignal`` (audience-segment kind).
  2. Storefront calls AdCP ``get_signals`` against the agent and sees the
     signal projected onto the AdCP ``Signal`` wire shape with
     ``adapter_config`` elided.
  3. Storefront passes the ``signal_id`` in
     ``TargetingOverlay.audience_include`` on ``create_media_buy``.
  4. GAM targeting manager resolves it into a line-item
     ``audienceTargeting.includedAudienceSegmentIds`` block.

Plus the parallel ``custom_key_value`` kind that lands in the shared
``custom_targeting`` accumulator, and the failure mode (unknown signal_id
raises a typed error).
"""

from __future__ import annotations

import asyncio

import pytest
from adcp.types.generated_poc.core.targeting import TargetingOverlay

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _SignalFlowEnv(IntegrationEnv):
    """Bare integration env — signals flow doesn't need external mocks."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestTenantSignalsDiscovery:
    """get_signals merges operator-declared TenantSignal rows alongside the
    hardcoded sample signals, projected onto the AdCP ``Signal`` shape with
    ``adapter_config`` elided.
    """

    def test_audience_signal_appears_in_get_signals(self, integration_db):
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import GetSignalsRequest
        from src.core.tools.signals import _get_signals_impl
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(
                tenant_id="sig_disc_t1",
                ad_server="google_ad_manager",
                public_agent_url="https://disc.example.com/agent",
            )
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports_fans",
                name="Sports Fans",
                value_type="binary",
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
                targeting_dimension="audience",
                data_provider="publisher_1p",
            )

            identity = ResolvedIdentity(
                tenant_id="sig_disc_t1",
                principal_id=None,
                tenant={
                    "ad_server": "google_ad_manager",
                    "public_agent_url": "https://disc.example.com/agent",
                },
                principal=None,
                testing_context=None,
                auth_method="api_key",
                raw_credential=None,
            )
            response = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=identity))

        target = [s for s in response.signals if s.signal_agent_segment_id == "audience_sports_fans"]
        assert len(target) == 1, "operator-declared signal should appear in get_signals response"
        wire = target[0].model_dump(mode="json")
        assert wire["value_type"] == "binary"
        assert wire["data_provider"] == "publisher_1p"
        # adapter_config is operator-only — must never appear on the wire.
        assert "adapter_config" not in wire

    def test_numeric_range_signal_carries_range(self, integration_db):
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import GetSignalsRequest
        from src.core.tools.signals import _get_signals_impl
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="sig_disc_t2", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="weather_temp_f",
                name="Temperature",
                value_type="numeric",
                range_min=-40,
                range_max=120,
                adapter_config={"kind": "custom_key_value", "key_id": "11111"},
                targeting_dimension="weather",
            )

            identity = ResolvedIdentity(
                tenant_id="sig_disc_t2",
                principal_id=None,
                tenant={"ad_server": "google_ad_manager"},
                principal=None,
                testing_context=None,
                auth_method="api_key",
                raw_credential=None,
            )
            response = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=identity))

        target = next(s for s in response.signals if s.signal_agent_segment_id == "weather_temp_f")
        wire = target.model_dump(mode="json")
        assert wire["value_type"] == "numeric"
        assert wire["range"] == {"min": -40.0, "max": 120.0}


class TestGamSignalResolution:
    """GAMTargetingManager._resolve_audience_signals translates buyer-supplied
    signal_ids into GAM line-item targeting via ``TenantSignal.adapter_config``.
    """

    def test_audience_include_resolves_to_audience_targeting(self, integration_db):
        from src.adapters.gam.managers.targeting import GAMTargetingManager
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_res_t1", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports_fans",
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
            )
            env.get_session()  # commit factory data

            tm = GAMTargetingManager(
                tenant_id="gam_res_t1",
                gam_client=None,
                targeting_config={
                    "custom_targeting_keys": {},
                    "axe_include_key": None,
                    "axe_exclude_key": None,
                    "axe_macro_key": None,
                },
            )
            custom_targeting: dict[str, str] = {}
            audience_block = tm._resolve_audience_signals(
                TargetingOverlay(audience_include=["audience_sports_fans"]),
                custom_targeting,
            )

        assert audience_block == {"includedAudienceSegmentIds": ["98765"]}
        assert custom_targeting == {}, "audience-segment signals must not pollute custom_targeting"

    def test_audience_exclude_resolves_to_excluded_block(self, integration_db):
        from src.adapters.gam.managers.targeting import GAMTargetingManager
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_res_t2", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_competitors",
                adapter_config={"kind": "audience_segment", "segment_id": "55555"},
            )
            env.get_session()

            tm = GAMTargetingManager(
                tenant_id="gam_res_t2",
                gam_client=None,
                targeting_config={
                    "custom_targeting_keys": {},
                    "axe_include_key": None,
                    "axe_exclude_key": None,
                    "axe_macro_key": None,
                },
            )
            audience_block = tm._resolve_audience_signals(
                TargetingOverlay(audience_exclude=["audience_competitors"]),
                {},
            )

        assert audience_block == {"excludedAudienceSegmentIds": ["55555"]}

    def test_custom_key_value_signal_layers_into_custom_targeting(self, integration_db):
        from src.adapters.gam.managers.targeting import GAMTargetingManager
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_res_t3", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="kv_vertical_news",
                adapter_config={
                    "kind": "custom_key_value",
                    "key_id": "11111",
                    "value_id": "22222",
                },
                targeting_dimension="contextual",
            )
            env.get_session()

            tm = GAMTargetingManager(
                tenant_id="gam_res_t3",
                gam_client=None,
                targeting_config={
                    "custom_targeting_keys": {},
                    "axe_include_key": None,
                    "axe_exclude_key": None,
                    "axe_macro_key": None,
                },
            )
            custom_targeting: dict[str, str] = {}

            # Include
            audience_block = tm._resolve_audience_signals(
                TargetingOverlay(audience_include=["kv_vertical_news"]),
                custom_targeting,
            )
            assert audience_block is None, "custom-KV signals must not surface in audienceTargeting"
            assert custom_targeting == {"11111": "22222"}

            # Exclude — mirrors AXE NOT_ prefix
            custom_targeting_exclude: dict[str, str] = {}
            tm._resolve_audience_signals(
                TargetingOverlay(audience_exclude=["kv_vertical_news"]),
                custom_targeting_exclude,
            )
            assert custom_targeting_exclude == {"NOT_11111": "22222"}

    def test_unknown_signal_id_raises_with_clear_message(self, integration_db):
        from src.adapters.gam.managers.targeting import GAMTargetingManager
        from tests.factories import TenantFactory

        with _SignalFlowEnv() as env:
            TenantFactory(tenant_id="gam_res_t4", ad_server="google_ad_manager")
            env.get_session()

            tm = GAMTargetingManager(
                tenant_id="gam_res_t4",
                gam_client=None,
                targeting_config={
                    "custom_targeting_keys": {},
                    "axe_include_key": None,
                    "axe_exclude_key": None,
                    "axe_macro_key": None,
                },
            )
            with pytest.raises(ValueError) as exc_info:
                tm._resolve_audience_signals(
                    TargetingOverlay(audience_include=["nope_unknown_signal"]),
                    {},
                )

        message = str(exc_info.value)
        assert "nope_unknown_signal" in message
        assert "gam_res_t4" in message

    def test_empty_overlay_returns_none(self, integration_db):
        from src.adapters.gam.managers.targeting import GAMTargetingManager
        from tests.factories import TenantFactory

        with _SignalFlowEnv() as env:
            TenantFactory(tenant_id="gam_res_t5", ad_server="google_ad_manager")
            env.get_session()

            tm = GAMTargetingManager(
                tenant_id="gam_res_t5",
                gam_client=None,
                targeting_config={
                    "custom_targeting_keys": {},
                    "axe_include_key": None,
                    "axe_exclude_key": None,
                    "axe_macro_key": None,
                },
            )
            custom_targeting: dict[str, str] = {}
            audience_block = tm._resolve_audience_signals(TargetingOverlay(), custom_targeting)

        assert audience_block is None
        assert custom_targeting == {}
