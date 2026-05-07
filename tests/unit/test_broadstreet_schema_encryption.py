"""Tests for Broadstreet schema-level Fernet encryption of api_key.

Mirrors the encryption tests for Triton + FreeWheel: round-trip, double-
encryption guard, plaintext-rehydration guard (legacy rows that predate the
encryption change).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.adapters.broadstreet.schemas import BroadstreetConnectionConfig
from src.core.utils.encryption import generate_encryption_key, is_encrypted


@pytest.fixture
def encryption_key():
    key = generate_encryption_key()
    with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
        yield key


class TestBroadstreetApiKeyEncryption:
    def test_api_key_serializes_to_ciphertext(self, encryption_key):
        cfg = BroadstreetConnectionConfig(network_id="123", api_key="bs-secret-token")
        dumped = cfg.model_dump()
        assert dumped["api_key"] != "bs-secret-token"
        assert is_encrypted(dumped["api_key"])

    def test_api_key_round_trips(self, encryption_key):
        original = BroadstreetConnectionConfig(network_id="123", api_key="bs-secret-token")
        rehydrated = BroadstreetConnectionConfig.model_validate(original.model_dump())
        assert rehydrated.api_key == "bs-secret-token"

    def test_already_encrypted_value_not_double_encrypted(self, encryption_key):
        cfg = BroadstreetConnectionConfig(network_id="123", api_key="bs-secret-token")
        ciphertext = cfg.model_dump()["api_key"]
        rehydrated = BroadstreetConnectionConfig.model_validate({"network_id": "123", "api_key": ciphertext})
        assert rehydrated.api_key == "bs-secret-token"

    def test_legacy_plaintext_loads_without_error(self, encryption_key):
        """Pre-encryption rows (plaintext api_key in config_json) must continue to load.

        The validator passes plaintext through unchanged when ``is_encrypted`` is
        False; the next ``model_dump()`` then encrypts it on save. This is the
        gradual-transition path documented in the schema docstring.
        """
        rehydrated = BroadstreetConnectionConfig.model_validate(
            {"network_id": "123", "api_key": "legacy-plaintext-token"}
        )
        assert rehydrated.api_key == "legacy-plaintext-token"
        # Re-dump now produces ciphertext — the next save persists the encrypted form.
        re_dumped = rehydrated.model_dump()
        assert is_encrypted(re_dumped["api_key"])

    def test_double_dump_validate_does_not_corrupt(self, encryption_key):
        """dump → validate → dump → validate must yield the original plaintext.

        Catches the re-encryption hazard where ``is_encrypted()`` failing to
        recognise a Fernet token would cause the field_serializer to encrypt
        ciphertext, producing un-decryptable double-encrypted data.
        """
        original = BroadstreetConnectionConfig(network_id="123", api_key="bs-secret")
        first_dump = original.model_dump()
        rehydrated = BroadstreetConnectionConfig.model_validate(first_dump)
        second_dump = rehydrated.model_dump()
        again = BroadstreetConnectionConfig.model_validate(second_dump)
        assert again.api_key == "bs-secret"
