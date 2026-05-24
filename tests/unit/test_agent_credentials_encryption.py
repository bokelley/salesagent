"""Agent auth credentials are encrypted at rest."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.core.database.models import CreativeAgent, SignalsAgent
from src.core.exceptions import AdCPConfigurationError
from src.core.utils.encryption import SECRET_CIPHERTEXT_PREFIX, is_encrypted


def test_creative_agent_auth_credentials_encrypt_at_rest(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    agent = CreativeAgent(
        tenant_id="tenant_1",
        agent_url="https://creative.example.com",
        name="Creative",
        auth_credentials="creative-secret",
    )

    assert agent.auth_credentials == "creative-secret"
    assert agent._auth_credentials != "creative-secret"
    assert agent._auth_credentials.startswith(SECRET_CIPHERTEXT_PREFIX)
    assert is_encrypted(agent._auth_credentials)


def test_signals_agent_auth_credentials_encrypt_at_rest(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    agent = SignalsAgent(
        tenant_id="tenant_1",
        agent_url="https://signals.example.com",
        name="Signals",
        auth_credentials="signals-secret",
    )

    assert agent.auth_credentials == "signals-secret"
    assert agent._auth_credentials != "signals-secret"
    assert agent._auth_credentials.startswith(SECRET_CIPHERTEXT_PREFIX)
    assert is_encrypted(agent._auth_credentials)


def test_legacy_plaintext_agent_credentials_still_read(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    creative = CreativeAgent(tenant_id="tenant_1", agent_url="https://creative.example.com", name="Creative")
    signals = SignalsAgent(tenant_id="tenant_1", agent_url="https://signals.example.com", name="Signals")
    creative._auth_credentials = "legacy-creative-secret"
    signals._auth_credentials = "legacy-signals-secret"

    assert creative.auth_credentials == "legacy-creative-secret"
    assert signals.auth_credentials == "legacy-signals-secret"


def test_legacy_fernet_looking_plaintext_agent_credentials_still_read(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    creative = CreativeAgent(tenant_id="tenant_1", agent_url="https://creative.example.com", name="Creative")
    creative._auth_credentials = "gAAAAA_plaintext_token"

    assert creative.auth_credentials == "gAAAAA_plaintext_token"


def test_wrong_key_prefixed_ciphertext_does_not_fall_back_to_plaintext(monkeypatch):
    old_key = Fernet.generate_key()
    ciphertext = f"{SECRET_CIPHERTEXT_PREFIX}{Fernet(old_key).encrypt(b'creative-secret').decode()}"
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    creative = CreativeAgent(tenant_id="tenant_1", agent_url="https://creative.example.com", name="Creative")
    creative._auth_credentials = ciphertext

    with pytest.raises(AdCPConfigurationError):
        _ = creative.auth_credentials
