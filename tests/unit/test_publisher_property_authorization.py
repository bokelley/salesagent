"""Tests for publisher-property authorization helpers."""

from __future__ import annotations

from src.admin.services import publisher_property_authorization


def test_local_example_authorization_seed_gate_allows_non_production_test_modes(monkeypatch):
    monkeypatch.setattr(publisher_property_authorization, "is_admin_production", lambda: False)
    monkeypatch.delenv("ADCP_TESTING", raising=False)
    monkeypatch.delenv("SEED_LOCAL_EXAMPLE_PUBLISHER_AUTHORIZATION", raising=False)
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")

    assert publisher_property_authorization.should_seed_local_example_publisher_authorization() is True


def test_local_example_authorization_seed_gate_blocks_production_even_with_overrides(monkeypatch):
    monkeypatch.setattr(publisher_property_authorization, "is_admin_production", lambda: True)
    monkeypatch.setenv("ADCP_TESTING", "true")
    monkeypatch.setenv("SEED_LOCAL_EXAMPLE_PUBLISHER_AUTHORIZATION", "true")
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")

    assert publisher_property_authorization.should_seed_local_example_publisher_authorization() is False
