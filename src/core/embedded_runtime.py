"""Runtime gates for embedded-instance capability ownership."""

from __future__ import annotations

from typing import Any

COMPOSE_PRODUCTS = "compose_products"
CREATIVE_APPROVAL = "creative_approval"
CAMPAIGN_APPROVAL = "campaign_approval"
AI_SERVICES = "ai_services"


def publisher_owns_runtime_capability(capability: str) -> bool:
    """Return whether this salesagent owns a runtime decision capability."""
    from src.admin.utils.embedded_capabilities import publisher_owns

    return publisher_owns(capability)


def publisher_owns_compose_products() -> bool:
    return publisher_owns_runtime_capability(COMPOSE_PRODUCTS)


def publisher_owns_creative_approval() -> bool:
    return publisher_owns_runtime_capability(CREATIVE_APPROVAL)


def publisher_owns_campaign_approval() -> bool:
    return publisher_owns_runtime_capability(CAMPAIGN_APPROVAL)


def publisher_owns_ai_services() -> bool:
    return publisher_owns_runtime_capability(AI_SERVICES)


def mark_compose_disabled(response: Any) -> Any:
    """Return product discovery without seller-side proposal composition."""
    response.proposals = []
    return response
