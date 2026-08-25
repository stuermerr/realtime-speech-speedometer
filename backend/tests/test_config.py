from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import (
    AzureSettings,
    ConfigurationError,
    DeepgramSettings,
    LiveWpmDebugSettings,
    load_backend_environment,
)


VALID_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://speech.example.openai.azure.com/",
    "AZURE_OPENAI_API_KEY": "super-secret-key",
    "AZURE_OPENAI_DEPLOYMENT": "live-transcribe",
}


def test_backend_environment_uses_explicit_file_and_process_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_env = tmp_path / "backend.env"
    backend_env.write_text(
        "FILE_ONLY=from-file\nSHARED=from-file\nSECRET_VALUE=file-secret\n"
    )
    unrelated_directory = tmp_path / "working-directory"
    unrelated_directory.mkdir()
    (unrelated_directory / ".env").write_text("UNRELATED=must-not-load\n")
    monkeypatch.chdir(unrelated_directory)

    environment = load_backend_environment(
        dotenv_path=backend_env,
        environment={"SHARED": "from-process", "PROCESS_ONLY": "from-process"},
    )

    assert environment == {
        "FILE_ONLY": "from-file",
        "SHARED": "from-process",
        "SECRET_VALUE": "file-secret",
        "PROCESS_ONLY": "from-process",
    }


def test_missing_backend_environment_file_is_safe(tmp_path: Path) -> None:
    environment = load_backend_environment(
        dotenv_path=tmp_path / "missing.env",
        environment={"PROCESS_ONLY": "from-process"},
    )

    assert environment == {"PROCESS_ONLY": "from-process"}


def test_settings_load_valid_azure_configuration() -> None:
    settings = AzureSettings.from_environment(VALID_ENV)

    assert settings.endpoint == "https://speech.example.openai.azure.com"
    assert settings.deployment == "live-transcribe"
    assert settings.websocket_url == (
        "wss://speech.example.openai.azure.com/openai/v1/realtime?intent=transcription"
    )


def test_settings_accept_azure_openai_v1_base_url() -> None:
    environment = {
        **VALID_ENV,
        "AZURE_OPENAI_ENDPOINT": ("https://speech.example.openai.azure.com/openai/v1"),
    }

    settings = AzureSettings.from_environment(environment)

    assert settings.endpoint == "https://speech.example.openai.azure.com"
    assert settings.websocket_url.endswith("/openai/v1/realtime?intent=transcription")


def test_missing_settings_report_names_without_values() -> None:
    secret = "must-not-appear"

    with pytest.raises(ConfigurationError) as caught:
        AzureSettings.from_environment(
            {
                "AZURE_OPENAI_ENDPOINT": "",
                "AZURE_OPENAI_API_KEY": secret,
                "AZURE_OPENAI_DEPLOYMENT": "",
            }
        )

    message = str(caught.value)
    assert "AZURE_OPENAI_ENDPOINT" in message
    assert "AZURE_OPENAI_DEPLOYMENT" in message
    assert secret not in message


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://speech.example.openai.azure.com",
        "speech.example.openai.azure.com",
        "https://user:password@speech.example.openai.azure.com",
        "https://speech.example.openai.azure.com/unexpected-path",
    ],
)
def test_invalid_endpoint_is_rejected_without_echoing_it(endpoint: str) -> None:
    environment = {**VALID_ENV, "AZURE_OPENAI_ENDPOINT": endpoint}

    with pytest.raises(ConfigurationError) as caught:
        AzureSettings.from_environment(environment)

    assert endpoint not in str(caught.value)


def test_deepgram_settings_load_server_side_api_key_without_exposing_it() -> None:
    secret = "deepgram-secret"

    settings = DeepgramSettings.from_environment({"DEEPGRAM_API_KEY": secret})

    assert settings.api_key == secret
    assert secret not in repr(settings)


def test_missing_deepgram_api_key_reports_only_variable_name() -> None:
    with pytest.raises(ConfigurationError) as caught:
        DeepgramSettings.from_environment({"DEEPGRAM_API_KEY": "  "})

    assert str(caught.value) == (
        "Missing required Deepgram configuration: DEEPGRAM_API_KEY"
    )


def test_live_wpm_debug_logging_is_disabled_by_default() -> None:
    settings = LiveWpmDebugSettings.from_environment({})

    assert settings.enabled is False


def test_live_wpm_debug_logging_requires_explicit_boolean_value() -> None:
    assert LiveWpmDebugSettings.from_environment({"LIVE_WPM_DEBUG": "true"}).enabled

    with pytest.raises(ConfigurationError) as caught:
        LiveWpmDebugSettings.from_environment({"LIVE_WPM_DEBUG": "provider-secret"})

    assert str(caught.value) == "LIVE_WPM_DEBUG must be true or false"
