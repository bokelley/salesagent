"""Authorization checks for publisher-property selectors."""

from __future__ import annotations

from typing import Any

from src.admin.api_schemas.publisher_properties import PublisherPropertySelector
from src.core.database.repositories.tenant_config import TenantConfigRepository


def validate_publisher_property_selectors(
    *,
    session: Any,
    tenant_id: str,
    selectors: list[PublisherPropertySelector],
    field_prefix: str = "publisher_properties",
) -> list[dict[str, str]]:
    authorized = TenantConfigRepository(session, tenant_id).list_authorized_properties()
    properties_by_domain: dict[str, list[Any]] = {}
    for prop in authorized:
        properties_by_domain.setdefault(prop.publisher_domain.lower(), []).append(prop)

    issues: list[dict[str, str]] = []
    for idx, selector in enumerate(selectors):
        selector_prefix = f"{field_prefix}.{idx}"
        domain = selector.publisher_domain.lower()
        domain_properties = properties_by_domain.get(domain, [])
        if not domain_properties:
            issues.append(
                {
                    "code": "publisher_domain_not_authorized",
                    "field": f"{selector_prefix}.publisher_domain",
                    "message": (
                        f"Publisher domain {selector.publisher_domain!r} has no authorized properties for this tenant."
                    ),
                }
            )
            continue

        if selector.selection_type == "by_id":
            authorized_ids = {prop.property_id for prop in domain_properties}
            missing_ids = sorted(set(selector.property_ids or []) - authorized_ids)
            if missing_ids:
                issues.append(
                    {
                        "code": "publisher_property_not_authorized",
                        "field": f"{selector_prefix}.property_ids",
                        "message": f"Publisher property id(s) are not authorized for {selector.publisher_domain}: "
                        f"{', '.join(missing_ids)}.",
                    }
                )
        elif selector.selection_type == "by_tag":
            authorized_tags = {tag for prop in domain_properties for tag in (prop.tags or [])}
            missing_tags = sorted(set(selector.property_tags or []) - authorized_tags)
            if missing_tags:
                issues.append(
                    {
                        "code": "publisher_property_tag_not_authorized",
                        "field": f"{selector_prefix}.property_tags",
                        "message": f"Publisher property tag(s) are not authorized for {selector.publisher_domain}: "
                        f"{', '.join(missing_tags)}.",
                    }
                )
    return issues
