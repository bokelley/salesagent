"""Tests for FreeWheel adapter schemas — encryption round-trip + validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS, FreeWheelConnectionConfig, FreeWheelProductConfig
from src.core.utils.encryption import generate_encryption_key, is_encrypted


@pytest.fixture
def encryption_key():
    key = generate_encryption_key()
    with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
        yield key


class TestFreeWheelConnectionConfig:
    def test_default_environment_is_production(self):
        cfg = FreeWheelConnectionConfig(api_token="tok")
        assert cfg.environment == "production"
        assert cfg.base_url == "https://api.freewheel.tv"

    def test_staging_environment_resolves_to_staging_host(self):
        cfg = FreeWheelConnectionConfig(api_token="tok", environment="staging")
        assert cfg.base_url == "https://api.stg.freewheel.tv"

    def test_invalid_environment_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FreeWheelConnectionConfig(api_token="tok", environment="dev")

    def test_api_token_serializes_to_ciphertext(self, encryption_key):
        cfg = FreeWheelConnectionConfig(api_token="super-secret-token")
        dumped = cfg.model_dump()
        assert dumped["api_token"] != "super-secret-token"
        assert is_encrypted(dumped["api_token"])

    def test_api_token_round_trips(self, encryption_key):
        original = FreeWheelConnectionConfig(api_token="super-secret-token")
        persisted = original.model_dump()
        rehydrated = FreeWheelConnectionConfig.model_validate(persisted)
        assert rehydrated.api_token == "super-secret-token"

    def test_already_encrypted_token_not_double_encrypted(self, encryption_key):
        cfg = FreeWheelConnectionConfig(api_token="super-secret-token")
        ciphertext = cfg.model_dump()["api_token"]
        rehydrated = FreeWheelConnectionConfig.model_validate({"api_token": ciphertext})
        assert rehydrated.api_token == "super-secret-token"

    def test_secret_marker_in_schema(self):
        schema = FreeWheelConnectionConfig.model_json_schema()
        assert schema["properties"]["api_token"].get("secret") is True

    def test_default_advertiser_id_optional(self):
        cfg = FreeWheelConnectionConfig(api_token="tok")
        assert cfg.default_advertiser_id is None

    def test_default_advertiser_id_accepts_string(self):
        cfg = FreeWheelConnectionConfig(api_token="tok", default_advertiser_id="1356511")
        assert cfg.default_advertiser_id == "1356511"

    def test_hosts_table_has_both_envs(self):
        assert "production" in FREEWHEEL_HOSTS
        assert "staging" in FREEWHEEL_HOSTS


class TestFreeWheelProductConfig:
    def test_defaults_are_empty(self):
        cfg = FreeWheelProductConfig()
        assert cfg.placement_ids == []
        assert cfg.targeting_profile_id is None
        assert cfg.priority is None
        assert cfg.custom_targeting == {}

    def test_accepts_full_product_config(self):
        cfg = FreeWheelProductConfig(
            placement_ids=["12345", "67890"],
            targeting_profile_id="tp_123",
            priority=10,
            custom_targeting={"genre": ["sports", "news"]},
        )
        assert cfg.placement_ids == ["12345", "67890"]
        assert cfg.priority == 10
        assert cfg.custom_targeting == {"genre": ["sports", "news"]}
