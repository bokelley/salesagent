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


def publisher_properties_dict_adagents(agent_url: str = "https://interchange.io") -> dict:
    """Return managed-network adagents.json using dict publisher_properties."""
    return {
        "properties": [
            managed_website_property("site_a", "a.example.com", "Site A"),
            managed_website_property("site_b", "b.example.com", "Site B"),
            managed_website_property("site_c", "c.example.com", "Site C"),
        ],
        "authorized_agents": [
            {
                "url": agent_url,
                "authorization_type": "publisher_properties",
                "publisher_properties": {
                    "publisher_domains": ["a.example.com", "b.example.com"],
                },
            }
        ],
    }
