"""Tests for shared adagents.json shape adapters."""

from src.services._adagents_shapes import get_authorized_properties_by_agent
from tests.helpers.adagents import managed_website_property, publisher_properties_dict_adagents


def test_dict_publisher_properties_resolves_by_delegating_to_sdk():
    adagents = publisher_properties_dict_adagents()

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert [prop["property_id"] for prop in properties] == ["site_a", "site_b"]


def test_dict_publisher_properties_missing_selection_type_infers_tags():
    adagents = publisher_properties_dict_adagents()
    adagents["authorized_agents"][0]["publisher_properties"]["publisher_domains"] = [
        "a.example.com",
        "b.example.com",
        "c.example.com",
    ]
    adagents["authorized_agents"][0]["publisher_properties"]["property_tags"] = ["premium"]
    adagents["properties"][0]["tags"] = ["premium"]
    adagents["properties"][1]["tags"] = ["managed"]
    adagents["properties"][2]["tags"] = ["premium"]

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert [prop["property_id"] for prop in properties] == ["site_a", "site_c"]


def test_dict_publisher_properties_missing_selection_type_infers_ids():
    adagents = publisher_properties_dict_adagents()
    adagents["authorized_agents"][0]["publisher_properties"]["property_ids"] = ["site_b"]

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert [prop["property_id"] for prop in properties] == ["site_b"]


def test_dict_publisher_properties_with_both_domain_fields_fails_closed():
    adagents = publisher_properties_dict_adagents()
    selector = adagents["authorized_agents"][0]["publisher_properties"]
    selector["publisher_domain"] = "a.example.com"

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert properties == []


def test_dict_publisher_properties_uses_sdk_exact_publisher_domain_matching():
    adagents = publisher_properties_dict_adagents()
    adagents["authorized_agents"][0]["publisher_properties"]["publisher_domains"] = ["example.com"]
    adagents["properties"] = [
        managed_website_property("mobile", "m.example.com", "Mobile"),
    ]

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert properties == []


def test_dict_publisher_properties_uses_sdk_exact_revocation_matching():
    adagents = publisher_properties_dict_adagents()
    adagents["revoked_publisher_domains"] = [{"publisher_domain": "m.example.com"}]
    adagents["properties"] = [
        managed_website_property("site_root", "example.com", "Root"),
        managed_website_property("site_mobile", "m.example.com", "Mobile"),
    ]
    adagents["authorized_agents"][0]["publisher_properties"]["publisher_domains"] = [
        "example.com",
        "m.example.com",
    ]

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert [prop["property_id"] for prop in properties] == ["site_root"]


def test_dict_publisher_properties_preserves_same_property_id_across_domains():
    adagents = publisher_properties_dict_adagents()
    adagents["properties"] = [
        managed_website_property("homepage", "a.example.com", "A Homepage"),
        managed_website_property("homepage", "b.example.com", "B Homepage"),
    ]

    properties = get_authorized_properties_by_agent(adagents, "https://interchange.io")

    assert [prop["name"] for prop in properties] == ["A Homepage", "B Homepage"]
