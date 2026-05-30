"""Authorization checks for publisher-property selectors."""

from __future__ import annotations

import os
from typing import Any

from src.admin.api_schemas.publisher_properties import PublisherPropertySelector
from src.admin.utils.helpers import is_admin_production
from src.core.database.repositories.tenant_config import TenantConfigRepository


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def should_seed_local_example_publisher_authorization() -> bool:
    """Return True when the local example.com fixture should self-heal."""
    if is_admin_production():
        return False
    return (
        _env_truthy("ADCP_TESTING")
        or _env_truthy("SEED_LOCAL_EXAMPLE_PUBLISHER_AUTHORIZATION")
        or _env_truthy("ADCP_AUTH_TEST_MODE")
    )


def seed_local_example_publisher_authorization(session: Any, tenant_id: str) -> None:
    """Install the example.com publisher fixture for local embedded E2E runs."""
    if not should_seed_local_example_publisher_authorization():
        return

    previous_management_caller = session.info.get("management_api_caller")
    session.info["management_api_caller"] = True
    try:
        TenantConfigRepository(session, tenant_id).ensure_example_publisher_authorization()
    finally:
        if previous_management_caller is None:
            session.info.pop("management_api_caller", None)
        else:
            session.info["management_api_caller"] = previous_management_caller


def seed_local_example_publisher_authorization_for_selectors(
    *,
    session: Any,
    tenant_id: str,
    selectors: list[PublisherPropertySelector],
) -> None:
    """Self-heal the local example.com fixture only when a selector needs it."""
    if any(selector.publisher_domain.lower() == "example.com" for selector in selectors):
        seed_local_example_publisher_authorization(session, tenant_id)


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
