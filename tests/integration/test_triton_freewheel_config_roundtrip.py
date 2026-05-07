"""Schema-level config round-trip for Triton + FreeWheel against the real DB.

Covers the persistence path *below* the Flask wrapper:

  TritonConnectionConfig / FreeWheelConnectionConfig (validate + encrypt)
    → AdapterConfig.config_json in PostgreSQL
    → on-disk: ciphertext for secret fields
    → tenant_status: is_configured / missing_config reporting
    → rehydrate: connection_config_class.model_validate decrypts to plaintext

This catches schema-drift bugs (e.g. a tenant_status check that reads a
field name the schema no longer exposes) and encryption-pipeline bugs (e.g.
a value that round-trips through model_dump → model_validate but doesn't
persist as ciphertext).

What's intentionally NOT covered here:
- The save_adapter_config Flask blueprint at /api/tenant/<id>/adapter-config
  (template parsing, secret-field preservation, ciphertext-replay rejection).
  That belongs at the admin/blueprint test layer with a Flask test client and
  is tracked as follow-up work — see docs/adapters/{triton,freewheel}/README.md
  for the credential-validation gates that need real OAuth endpoints anyway.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.adapters.freewheel import FreeWheelConnectionConfig
from src.adapters.triton import TritonConnectionConfig
from src.core.database.repositories.adapter_config import AdapterConfigRepository
from src.core.tenant_status import get_tenant_status, is_tenant_ad_server_configured
from src.core.utils.encryption import is_encrypted
from tests.factories.core import AdapterConfigFactory, TenantFactory
from tests.helpers.managed_tenant_api import bind_factories_to_session

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture
def _encryption_key():
    with patch.dict(os.environ, {"ENCRYPTION_KEY": _TEST_ENCRYPTION_KEY}):
        yield


def _persist_adapter_config(tenant_id: str, adapter_type: str, config_json: dict) -> None:
    """Persist a validated config_json for the tenant's adapter via the factory.

    Updates ``tenant.ad_server`` so ``tenant_status`` checks the new adapter
    type rather than whatever ``TenantFactory`` defaulted to.
    """
    from src.core.database.models import Tenant

    with bind_factories_to_session() as session:
        tenant = session.get(Tenant, tenant_id)
        tenant.ad_server = adapter_type
        existing = AdapterConfigRepository(session, tenant_id).find_by_tenant()
        if existing is not None:
            existing.adapter_type = adapter_type
            existing.config_json = config_json
        else:
            # Pass the live ORM instance so AdapterConfigFactory's SubFactory
            # doesn't try to mint a new tenant — overriding tenant_id alone
            # is ignored because the LazyAttribute reads off the SubFactory.
            AdapterConfigFactory(tenant=tenant, adapter_type=adapter_type, config_json=config_json)
        session.commit()


@pytest.fixture
def _tenant(integration_db, _encryption_key):
    """Yield a fresh tenant_id with default mock adapter config.

    Note: read ``tenant_id`` inside the binding context — the ORM instance
    gets detached when the session closes, and any attribute access after
    that triggers a refresh on a closed session.
    """
    tenant_id = "tenant_cfg_roundtrip"
    with bind_factories_to_session():
        TenantFactory(tenant_id=tenant_id, subdomain="cfg-roundtrip", ad_server="mock")
    yield tenant_id


def _read_config_json(tenant_id: str) -> dict:
    """Read AdapterConfig.config_json via the repository — the only path that
    satisfies the no-raw-select architecture guard.
    """
    with bind_factories_to_session() as session:
        row = AdapterConfigRepository(session, tenant_id).get_by_tenant()
        # config_json is mutable; copy so the caller can use it after the
        # session closes.
        return dict(row.config_json or {})


@pytest.mark.integration
@pytest.mark.requires_db
class TestTritonConfigRoundtrip:
    """Triton config persists encrypted, rehydrates plaintext, reports configured."""

    def test_password_auth_full_roundtrip(self, _tenant):
        validated = TritonConnectionConfig(
            username="alice@publisher.example",
            password="hunter2",
        )
        _persist_adapter_config(_tenant, "triton", validated.model_dump())

        # On-disk: ciphertext
        on_disk = _read_config_json(_tenant)
        assert is_encrypted(on_disk["password"]), "password must be ciphertext at rest"
        assert on_disk["username"] == "alice@publisher.example"
        assert on_disk["auth_type"] == "password"

        # Tenant status reports configured
        assert is_tenant_ad_server_configured(_tenant) is True
        status = get_tenant_status(_tenant)
        assert status["is_configured"] is True
        assert status["adapter_type"] == "triton"
        assert status["missing_config"] == []

        # Rehydration through the schema yields plaintext
        rehydrated = TritonConnectionConfig.model_validate(on_disk)
        assert rehydrated.password == "hunter2"

    def test_oauth_client_credentials_auth_roundtrip(self, _tenant):
        validated = TritonConnectionConfig(
            username="client_id_xyz",
            password="client_secret_abc",
            auth_type="oauth_client_credentials",
        )
        _persist_adapter_config(_tenant, "triton", validated.model_dump())

        on_disk = _read_config_json(_tenant)
        assert on_disk["auth_type"] == "oauth_client_credentials"
        assert is_encrypted(on_disk["password"])

        rehydrated = TritonConnectionConfig.model_validate(on_disk)
        assert rehydrated.password == "client_secret_abc"

    def test_missing_password_reports_unconfigured(self, _tenant):
        # config_json with username but no password (simulates partial provisioning)
        _persist_adapter_config(_tenant, "triton", {"username": "alice@publisher.example"})

        assert is_tenant_ad_server_configured(_tenant) is False
        status = get_tenant_status(_tenant)
        assert status["is_configured"] is False
        assert any("password" in m.lower() for m in status["missing_config"])


@pytest.mark.integration
@pytest.mark.requires_db
class TestFreeWheelConfigRoundtrip:
    """FreeWheel config persists encrypted, rehydrates plaintext, reports configured."""

    def test_full_roundtrip_staging(self, _tenant):
        validated = FreeWheelConnectionConfig(
            client_id="fw_client_xyz",
            client_secret="fw_secret_abc",
            network_id="9876",
            environment="staging",
        )
        _persist_adapter_config(_tenant, "freewheel", validated.model_dump())

        on_disk = _read_config_json(_tenant)
        assert is_encrypted(on_disk["client_secret"]), "client_secret must be ciphertext at rest"
        assert on_disk["client_id"] == "fw_client_xyz"
        assert on_disk["network_id"] == "9876"
        assert on_disk["environment"] == "staging"

        assert is_tenant_ad_server_configured(_tenant) is True
        status = get_tenant_status(_tenant)
        assert status["is_configured"] is True
        assert status["adapter_type"] == "freewheel"

        rehydrated = FreeWheelConnectionConfig.model_validate(on_disk)
        assert rehydrated.client_secret == "fw_secret_abc"
        assert rehydrated.base_url == "https://api.stg.freewheel.tv"

    def test_missing_network_id_reports_unconfigured(self, _tenant):
        _persist_adapter_config(
            _tenant,
            "freewheel",
            {
                "client_id": "fw_client_xyz",
                "client_secret": "fw_secret_abc",
                # network_id missing
            },
        )
        assert is_tenant_ad_server_configured(_tenant) is False
        status = get_tenant_status(_tenant)
        assert any("network_id" in m for m in status["missing_config"])
