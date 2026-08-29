from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from application.settings import SettingsService
from application.secret_store import ProcessSecretStore


@pytest.fixture
def test_services(tmp_path: Path):
    services = create_application_services(tmp_path / "api_data")
    return services


@pytest.fixture
def client(test_services):
    app.dependency_overrides[get_services] = lambda: test_services
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_settings_service_load_save_mask(tmp_path: Path):
    settings_file = tmp_path / "settings.json"
    secret_store = ProcessSecretStore(str(tmp_path / "test-secret"))
    service = SettingsService(settings_file, secret_store=secret_store)

    # Test load defaults
    defaults = service.load_settings()
    assert defaults["provider"] == "OpenAI"
    assert defaults["model_name"] == "gpt-5.4-mini"
    assert defaults["api_key"] == ""

    # Test save
    service.save_settings({
        "provider": "Anthropic",
        "model_name": "claude-3-opus",
        "api_key": "my-secret-key-12345",
        "api_base": "https://api.anthropic.com"
    })

    saved = service.load_settings()
    assert saved["provider"] == "Anthropic"
    assert saved["model_name"] == "claude-3-opus"
    assert saved["api_key"] == "my-secret-key-12345"
    assert saved["api_base"] == "https://api.anthropic.com"
    public_data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "api_key" not in public_data
    assert "my-secret-key-12345" not in settings_file.read_text(encoding="utf-8")

    # Test mask
    masked = service.get_settings_masked()
    assert masked["api_key"] == "my-s...2345"

    # Test save key preservation (with mask)
    service.save_settings({
        "provider": "Anthropic",
        "model_name": "claude-3-opus",
        "api_key": "my-s...2345",
        "api_base": "https://api.anthropic.com"
    })
    preserved = service.load_settings()
    assert preserved["api_key"] == "my-secret-key-12345"

    service.clear_api_key()
    assert service.load_settings()["api_key"] == ""


def test_settings_service_migrates_legacy_plaintext_key(tmp_path: Path):
    settings_file = tmp_path / "settings.json"
    legacy_key = "legacy-plain-secret"
    settings_file.write_text(
        json.dumps({
            "provider": "OpenAI",
            "model_name": "gpt-test",
            "api_base": "",
            "api_key": legacy_key,
        }),
        encoding="utf-8",
    )
    secret_store = ProcessSecretStore(str(tmp_path / "migration-secret"))
    service = SettingsService(settings_file, secret_store=secret_store)

    assert service.load_settings()["api_key"] == legacy_key
    assert legacy_key not in settings_file.read_text(encoding="utf-8")
    assert "api_key" not in json.loads(settings_file.read_text(encoding="utf-8"))


def test_default_secret_store_never_writes_plaintext_key(tmp_path: Path):
    settings_file = tmp_path / "settings.json"
    secret = "default-store-secret-value"
    service = SettingsService(settings_file)
    service.save_settings({
        "provider": "OpenAI",
        "model_name": "gpt-test",
        "api_key": secret,
        "api_base": "",
    })

    assert secret not in settings_file.read_text(encoding="utf-8")
    if service.storage_status()["persistent"]:
        secret_file = settings_file.with_suffix(".secret")
        assert secret_file.exists()
        assert secret.encode("utf-8") not in secret_file.read_bytes()
        assert SettingsService(settings_file).load_settings()["api_key"] == secret


@patch("litellm.completion")
def test_settings_service_check_availability(mock_completion, tmp_path: Path):
    settings_file = tmp_path / "settings.json"
    service = SettingsService(settings_file)

    # Test no key
    res = service.check_availability()
    assert res["available"] is False
    assert "No API Key configured" in res["message"]

    # Test successful connection
    mock_completion.return_value = MagicMock()
    service.save_settings({
        "provider": "OpenAI",
        "model_name": "gpt-5.4-mini",
        "api_key": "testkey",
        "api_base": ""
    })

    res = service.check_availability()
    assert res["available"] is True
    assert res["mode"] == "byok"
    assert "Connection successful!" in res["message"]

    # A masked value submitted by the settings page resolves to the stored key.
    masked = service.get_settings_masked()
    res = service.check_availability(masked)
    assert res["available"] is True
    assert mock_completion.call_args.kwargs["api_key"] == "testkey"

    # Test connection exception
    mock_completion.side_effect = Exception("API Error")
    res = service.check_availability()
    assert res["available"] is False
    assert res["message"] == (
        "Connection failed. Verify the provider, model, endpoint, and API key."
    )
    assert "API Error" not in res["message"]


def test_settings_api_endpoints(client, test_services):
    # Test GET settings (empty initially)
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["api_key"] == ""
    assert data["model_name"] == "gpt-5.4-mini"

    # Test POST settings
    resp = client.post("/api/v1/settings", json={
        "provider": "OpenAI",
        "model_name": "gpt-5.4-mini",
        "api_key": "my-openai-key-secret",
        "api_base": "http://localhost:8080"
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["api_key"] == "my-o...cret"
    assert data["api_base"] == "http://localhost:8080"

    # Test status check endpoint
    with patch("litellm.completion") as mock_comp:
        mock_comp.return_value = MagicMock()
        resp = client.get("/api/v1/settings/status")
        assert resp.status_code == 200
        status_data = resp.json()["data"]
        assert status_data["available"] is True
        assert status_data["mode"] == "byok"

    # Test temp config test endpoint
    with patch("litellm.completion") as mock_comp:
        mock_comp.side_effect = Exception("Temp test failure")
        resp = client.post("/api/v1/settings/test", json={
            "provider": "Anthropic",
            "model_name": "claude-3-opus",
            "api_key": "tempkey",
            "api_base": ""
        })
        assert resp.status_code == 200
        test_data = resp.json()["data"]
        assert test_data["available"] is False
        assert "Temp test failure" not in test_data["message"]

    resp = client.delete("/api/v1/settings/api-key")
    assert resp.status_code == 200
    cleared = resp.json()["data"]
    assert cleared["api_key"] == ""
    assert cleared["api_key_configured"] is False


def test_settings_run_service_injection(test_services):
    # Save settings credentials
    test_services.settings.save_settings({
        "provider": "OpenAI",
        "model_name": "custom-gpt-model",
        "api_key": "stored-run-key",
        "api_base": "http://custom-base"
    })

    # Start design run without explicit credentials
    # We mock start_design_run to check what it gets passed
    with patch("application.services.start_design_run") as mock_start:
        test_services.runs.start({
            "user_intent": "Make something green",
            "host_organism": "E. coli"
        })
        mock_start.assert_called_once()
        kwargs = mock_start.call_args[1]
        assert kwargs["model_name"] == "custom-gpt-model"
        assert kwargs["api_key"] == "stored-run-key"
        assert kwargs["api_base"] == "http://custom-base"


def test_settings_service_cello_fields(tmp_path: Path):
    settings_file = tmp_path / "settings.json"
    service = SettingsService(settings_file)

    # 1. Verify defaults
    settings = service.load_settings()
    assert settings["cello_command"] == ""
    assert settings["ucf_path"] == ""
    assert settings["sensor_path"] == ""
    assert settings["device_path"] == ""
    assert settings["default_host"] == "Escherichia coli"
    assert settings["default_compute_budget"] == 6

    # 2. Verify save and load
    service.save_settings({
        "cello_command": "docker run cello",
        "ucf_path": "/path/to/ucf",
        "sensor_path": "/path/to/input.json",
        "device_path": "/path/to/output.json",
        "default_host": "Saccharomyces cerevisiae",
        "default_compute_budget": 12,
    })

    loaded = service.load_settings()
    assert loaded["cello_command"] == "docker run cello"
    assert loaded["ucf_path"] == "/path/to/ucf"
    assert loaded["sensor_path"] == "/path/to/input.json"
    assert loaded["device_path"] == "/path/to/output.json"
    assert loaded["default_host"] == "Saccharomyces cerevisiae"
    assert loaded["default_compute_budget"] == 12


def test_web_settings_page_get(client, test_services):
    resp = client.get("/web/settings")
    assert resp.status_code == 200
    assert "金鑰與模型設定" in resp.text
    assert "Cello 與物理映射配置" in resp.text
    assert "Cello 工具偵測狀態" in resp.text
    assert 'value="cello21"' in resp.text
    assert "Cello 2.1 (research preview)" in resp.text
    assert "Escherichia coli" in resp.text


def test_web_settings_page_post_success(client, test_services):
    resp = client.post(
        "/web/settings",
        data={
            "provider": "LiteLLM",
            "model_name": "custom-llm",
            "api_key": "some-api-key",
            "api_base": "http://my-endpoint",
            "cello_command": "docker run -v C:\\ucf:/data cello",
            "cello_artifact_format": "cello21",
            "ucf_path": "C:\\ucf\\Eco1C1G1T1.UCF.json",
            "sensor_path": "C:\\ucf\\Eco1C1G1T1.input.json",
            "device_path": "C:\\ucf\\Eco1C1G1T1.output.json",
            "default_host": "Bacillus subtilis",
            "default_compute_budget": "15"
        }
    )
    assert resp.status_code == 200
    assert "設定儲存成功！" in resp.text
    assert "docker run -v" in resp.text
    assert "Eco1C1G1T1.UCF.json" in resp.text
    assert "Bacillus subtilis" in resp.text

    # Verify values were persisted in SettingsService
    settings = test_services.settings.load_settings()
    assert settings["provider"] == "LiteLLM"
    assert settings["model_name"] == "custom-llm"
    assert settings["cello_command"] == "docker run -v C:\\ucf:/data cello"
    assert settings["cello_artifact_format"] == "cello21"
    assert settings["ucf_path"] == "C:\\ucf\\Eco1C1G1T1.UCF.json"
    assert settings["sensor_path"] == "C:\\ucf\\Eco1C1G1T1.input.json"
    assert settings["device_path"] == "C:\\ucf\\Eco1C1G1T1.output.json"
    assert settings["default_host"] == "Bacillus subtilis"
    assert settings["default_compute_budget"] == 15


def test_web_settings_success_messages_follow_selected_language(client):
    client.cookies.set("lang", "en")
    resp = client.post(
        "/web/settings",
        data={
            "provider": "LiteLLM",
            "model_name": "custom-llm",
            "api_key": "some-api-key",
            "api_base": "http://my-endpoint",
            "cello_command": "cello",
            "ucf_path": "C:/ucf/example.json",
            "default_host": "Escherichia coli",
            "default_compute_budget": "6",
        },
    )

    assert resp.status_code == 200
    assert "Settings saved successfully." in resp.text
    assert "設定儲存成功！" not in resp.text


def test_web_settings_accepts_omitted_optional_fields(client, test_services):
    resp = client.post(
        "/web/settings",
        data={
            "provider": "Gemini",
            "model_name": "gemini/gemini-2.5-flash",
        },
    )

    assert resp.status_code == 200
    settings = test_services.settings.load_settings()
    assert settings["api_base"] == ""
    assert settings["cello_command"] == ""
    assert settings["ucf_path"] == ""
    assert settings["sensor_path"] == ""
    assert settings["device_path"] == ""


def test_web_settings_delete_api_key(client, test_services):
    test_services.settings.save_settings({"api_key": "storedkey"})

    resp = client.post("/web/settings/api-key/delete")
    assert resp.status_code == 200
    assert "金鑰已清除！" in resp.text
    assert test_services.settings.load_settings()["api_key"] == ""


def test_settings_run_service_cello_injection(test_services):
    # Save settings including Cello params
    test_services.settings.save_settings({
        "cello_command": "stored-cello-cmd",
        "ucf_path": "stored-ucf-path",
        "sensor_path": "stored-sensor-path",
        "device_path": "stored-device-path",
        "default_host": "Bacillus subtilis",
        "default_compute_budget": 18
    })

    # Start design run without explicit Cello parameters
    with patch("application.services.start_design_run") as mock_start:
        test_services.runs.start({
            "user_intent": "Express RFP",
        })
        mock_start.assert_called_once()
        kwargs = mock_start.call_args[1]
        assert kwargs["cello_command"] == "stored-cello-cmd"
        assert kwargs["ucf_path"] == "stored-ucf-path"
        assert kwargs["sensor_path"] == "stored-sensor-path"
        assert kwargs["device_path"] == "stored-device-path"
        assert kwargs["host_organism"] == "Bacillus subtilis"
        assert kwargs["compute_budget"] == 18
