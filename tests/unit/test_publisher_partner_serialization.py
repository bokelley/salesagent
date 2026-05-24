"""Publisher partner API serialization regressions."""

from datetime import UTC, datetime

from src.admin.blueprints.publisher_partners import _partner_to_dict
from src.core.database.models import PublisherPartner


def test_legacy_unrefreshed_partner_does_not_project_verified_fallback_count():
    partner = PublisherPartner(
        id=1,
        tenant_id="tenant_1",
        publisher_domain="publisher.example",
        display_name="Publisher",
        is_verified=True,
        sync_status="success",
        total_properties=None,
        authorized_properties=None,
        aao_status_kind=None,
        created_at=datetime.now(UTC),
    )

    result = _partner_to_dict(partner)

    assert result["aao_status"] == "stale"
    assert result["is_verified"] is False
    assert result["total_properties"] == 0
    assert result["authorized_properties"] == 0
    assert result["property_count"] == 0
