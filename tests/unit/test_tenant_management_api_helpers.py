import pytest

from src.adapters.freewheel import FreeWheelConnectionConfig
from src.admin.api_schemas.tenant_management import (
    BroadstreetAdapterConfig,
    FreeWheelAdapterConfig,
    GAMAdapterConfig,
    SpringServeAdapterConfig,
)
from src.admin.tenant_management_api import (
    _adapter_probe_config,
    _persist_adapter_config,
    _set_adapter_manual_approval_required,
)
from src.core.database.models import AdapterConfig

TEST_ENCRYPTION_KEY = "PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk="


def test_adapter_probe_config_prefers_gam_service_account_over_refresh_token(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    adapter = AdapterConfig(
        tenant_id="tenant_gam_probe",
        adapter_type="google_ad_manager",
        gam_network_code="12345",
        gam_refresh_token="oauth-refresh-token",
    )
    adapter.gam_service_account_json = '{"type":"service_account"}'

    config = _adapter_probe_config(adapter)

    assert config["network_code"] == "12345"
    assert config["service_account_json"] == '{"type":"service_account"}'
    assert "refresh_token" not in config


def test_adapter_probe_config_decrypts_schema_adapter_secrets(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    stored = FreeWheelConnectionConfig(
        username="user@example.com",
        password="freewheel-password",
        environment="staging",
    ).model_dump()
    assert stored["password"] != "freewheel-password"

    adapter = AdapterConfig(
        tenant_id="tenant_freewheel_probe",
        adapter_type="freewheel",
        config_json=stored,
    )

    config = _adapter_probe_config(adapter)

    assert config["username"] == "user@example.com"
    assert config["password"] == "freewheel-password"
    assert config["environment"] == "staging"


class _NoExistingAdapter:
    def first(self):
        return None


class _RecordingSession:
    def __init__(self):
        self.added = None

    def scalars(self, _stmt):
        return _NoExistingAdapter()

    def add(self, adapter):
        self.added = adapter


@pytest.mark.parametrize(
    "adapter_schema",
    [
        FreeWheelAdapterConfig(type="freewheel", username="user@example.com", password="freewheel-password"),
        BroadstreetAdapterConfig(type="broadstreet", network_id="network-1", api_key="broadstreet-key"),
        SpringServeAdapterConfig(type="springserve", email="user@example.com", password="springserve-password"),
    ],
)
def test_persist_adapter_config_threads_manual_approval_into_config_json(adapter_schema, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    session = _RecordingSession()

    adapter = _persist_adapter_config(
        session,
        "tenant_schema_adapter",
        adapter_schema,
        manual_approval_required=True,
    )

    assert session.added is adapter
    assert adapter.config_json["manual_approval_required"] is True


def test_persist_adapter_config_threads_manual_approval_into_gam_column(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    session = _RecordingSession()

    adapter = _persist_adapter_config(
        session,
        "tenant_gam_manual_approval",
        GAMAdapterConfig(
            type="google_ad_manager",
            network_code="12345",
            service_account_email="sa@example.com",
            service_account_key_json='{"type":"service_account"}',
        ),
        manual_approval_required=True,
    )

    assert session.added is adapter
    assert adapter.gam_manual_approval_required is True


def test_set_adapter_manual_approval_required_updates_schema_config_json():
    adapter = AdapterConfig(
        tenant_id="tenant_freewheel_manual_approval",
        adapter_type="freewheel",
        config_json={"username": "user@example.com", "manual_approval_required": False},
    )

    _set_adapter_manual_approval_required(adapter, True)

    assert adapter.config_json["manual_approval_required"] is True
