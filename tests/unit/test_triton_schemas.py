"""Tests for Triton TAP adapter schemas — encryption round-trip + validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.adapters.triton.schemas import TritonConnectionConfig, TritonProductConfig
from src.core.utils.encryption import generate_encryption_key, is_encrypted


@pytest.fixture
def encryption_key():
    """Provide a deterministic Fernet key for the test session."""
    key = generate_encryption_key()
    with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
        yield key


class TestTritonConnectionConfig:
    def test_defaults_match_real_tap_endpoints(self):
        cfg = TritonConnectionConfig(username="alice@example.com", password="hunter2")
        assert cfg.base_url == "https://mbapi.tritondigital.com"
        assert cfg.login_url == "https://login.tritondigital.com"

    def test_password_serializes_to_ciphertext(self, encryption_key):
        cfg = TritonConnectionConfig(username="alice@example.com", password="hunter2")
        dumped = cfg.model_dump()
        assert dumped["password"] != "hunter2"
        assert is_encrypted(dumped["password"])

    def test_password_round_trips_through_persisted_dict(self, encryption_key):
        original = TritonConnectionConfig(username="alice@example.com", password="hunter2")
        persisted = original.model_dump()
        rehydrated = TritonConnectionConfig.model_validate(persisted)
        # In-memory model exposes plaintext after rehydration
        assert rehydrated.password == "hunter2"

    def test_already_encrypted_value_is_not_double_encrypted(self, encryption_key):
        cfg = TritonConnectionConfig(username="alice@example.com", password="hunter2")
        ciphertext = cfg.model_dump()["password"]
        # Re-dump from the rehydrated model — ciphertext should round-trip identically
        rehydrated = TritonConnectionConfig.model_validate({"username": "alice@example.com", "password": ciphertext})
        assert rehydrated.password == "hunter2"

    def test_secret_marker_in_schema(self):
        schema = TritonConnectionConfig.model_json_schema()
        assert schema["properties"]["password"].get("secret") is True

    def test_auth_type_default_is_password(self):
        cfg = TritonConnectionConfig(username="alice@example.com", password="hunter2")
        assert cfg.auth_type == "password"

    def test_oauth_client_credentials_auth_type_accepted(self):
        cfg = TritonConnectionConfig(
            username="client-id-abc", password="client-secret-xyz", auth_type="oauth_client_credentials"
        )
        assert cfg.auth_type == "oauth_client_credentials"

    def test_invalid_auth_type_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TritonConnectionConfig(username="alice", password="hunter2", auth_type="basic")

    def test_http_login_url_rejected(self):
        """Tenant admin must not be able to redirect credential POST to attacker host."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="https://"):
            TritonConnectionConfig(
                username="alice@example.com", password="hunter2", login_url="http://attacker.example/oauth2/token"
            )

    def test_http_base_url_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="https://"):
            TritonConnectionConfig(username="alice@example.com", password="hunter2", base_url="http://attacker.example")


class TestTritonProductConfig:
    def test_defaults_are_empty_lists(self):
        cfg = TritonProductConfig()
        assert cfg.station_ids == []
        assert cfg.station_group_ids == []
        assert cfg.genres == []

    def test_accepts_station_selection(self):
        cfg = TritonProductConfig(station_ids=["KROQ", "KIIS"], genres=["Rock", "Pop"])
        assert cfg.station_ids == ["KROQ", "KIIS"]
        assert cfg.genres == ["Rock", "Pop"]
