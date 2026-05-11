"""Integration tests for list_creatives `filters.creative_ids` (closes #318, #316).

Regression coverage for the bug where `_list_creatives_impl` extracted the
`filters` payload but only forwarded flat scalar params to
`CreativeRepository.get_by_principal()`. As a result the structured filters
on `CreativeFilters` (`creative_ids`, `format_ids`, `statuses`) were silently
dropped and the seller returned every creative in the principal's library.

Storyboard manifestation: `media_buy_seller/creative_fate_after_cancellation/
list_creatives_after_cancel` filtered to a single creative id and got back
the entire library, with `creatives[0]` being an unrelated leftover.
"""

from __future__ import annotations

import pytest
from adcp import CreativeFilters

from tests.factories import CreativeFactory, PrincipalFactory, TenantFactory
from tests.harness import CreativeListEnv, make_identity

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_three_creatives(env: CreativeListEnv) -> tuple[str, str]:
    """Seed three creatives for one principal. Returns (tenant_id, principal_id)."""
    tenant = TenantFactory(tenant_id="filter_creative_ids_tenant")
    principal = PrincipalFactory(tenant=tenant, principal_id="advertiser_1")
    for cid in ("acme_reuse_banner_001", "acme_other_002", "acme_third_003"):
        CreativeFactory(
            tenant=tenant,
            principal=principal,
            creative_id=cid,
            name=f"creative {cid}",
        )
    return tenant.tenant_id, principal.principal_id


def _identity_for(tenant_id: str, principal_id: str):
    return make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": tenant_id},
    )


class TestCreativeIdsFilter:
    """`filters.creative_ids` must be applied as a SQL IN-clause."""

    def test_creative_ids_filter_returns_only_matching(self, integration_db):
        """Filter to one creative_id of three → exactly that one returned."""
        with CreativeListEnv() as env:
            tenant_id, principal_id = _seed_three_creatives(env)
            response = env.call_impl(
                identity=_identity_for(tenant_id, principal_id),
                filters=CreativeFilters(creative_ids=["acme_reuse_banner_001"]),
            )

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "acme_reuse_banner_001"
        assert response.query_summary.total_matching == 1

    def test_creative_ids_filter_with_no_match_returns_empty(self, integration_db):
        """Filter to a bogus id → empty list (NOT the full library)."""
        with CreativeListEnv() as env:
            tenant_id, principal_id = _seed_three_creatives(env)
            response = env.call_impl(
                identity=_identity_for(tenant_id, principal_id),
                filters=CreativeFilters(creative_ids=["definitely_does_not_exist_xyz"]),
            )

        assert response.creatives == []
        assert response.query_summary.total_matching == 0

    def test_no_filter_returns_all(self, integration_db):
        """Control: no filter → all three creatives."""
        with CreativeListEnv() as env:
            tenant_id, principal_id = _seed_three_creatives(env)
            response = env.call_impl(identity=_identity_for(tenant_id, principal_id))

        assert len(response.creatives) == 3
        assert response.query_summary.total_matching == 3

    def test_creative_ids_filter_subset_returns_subset(self, integration_db):
        """Filter to two of three ids → exactly those two."""
        with CreativeListEnv() as env:
            tenant_id, principal_id = _seed_three_creatives(env)
            response = env.call_impl(
                identity=_identity_for(tenant_id, principal_id),
                filters=CreativeFilters(creative_ids=["acme_reuse_banner_001", "acme_third_003"]),
            )

        ids = {c.creative_id for c in response.creatives}
        assert ids == {"acme_reuse_banner_001", "acme_third_003"}
        assert response.query_summary.total_matching == 2

    def test_creative_ids_appears_in_filters_applied(self, integration_db):
        """Audit metadata records that creative_ids was applied."""
        with CreativeListEnv() as env:
            tenant_id, principal_id = _seed_three_creatives(env)
            response = env.call_impl(
                identity=_identity_for(tenant_id, principal_id),
                filters=CreativeFilters(creative_ids=["acme_reuse_banner_001"]),
            )

        assert any("creative_ids=" in entry for entry in (response.query_summary.filters_applied or []))


class TestStatusesFilter:
    """`filters.statuses` (plural) must filter, parallel to flat `status` param."""

    def test_statuses_filter_returns_only_matching(self, integration_db):
        """Two creatives in different statuses, filter to one → only that one."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="statuses_filter_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="advertiser_1")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="approved_one",
                status="approved",
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="pending_one",
                status="pending_review",
            )

            response = env.call_impl(
                identity=_identity_for(tenant.tenant_id, principal.principal_id),
                filters=CreativeFilters(statuses=["approved"]),
            )

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "approved_one"
