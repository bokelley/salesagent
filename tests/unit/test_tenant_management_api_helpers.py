from src.adapters.freewheel import FreeWheelConnectionConfig
from src.admin.tenant_management_api import _adapter_probe_config
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
