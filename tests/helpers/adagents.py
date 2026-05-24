"""Helpers for constructing adagents.json test fixtures."""


def managed_website_property(property_id: str, domain: str, name: str) -> dict:
    """Return a managed website property fixture."""
    return {
        "property_id": property_id,
        "property_type": "website",
        "name": name,
        "identifiers": [{"type": "domain", "value": domain}],
        "publisher_domain": domain,
        "tags": ["managed"],
    }
