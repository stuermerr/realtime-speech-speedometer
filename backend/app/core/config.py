from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values


BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]
BACKEND_ENV_PATH = BACKEND_DIRECTORY / ".env"


class ConfigurationError(RuntimeError):
    """Raised when server-side configuration cannot be used safely."""


def load_backend_environment(
    *,
    dotenv_path: Path = BACKEND_ENV_PATH,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return backend file configuration overlaid by process environment."""
    process_environment = os.environ if environment is None else environment
    file_environment = {
        name: value
        for name, value in dotenv_values(dotenv_path).items()
        if value is not None
    }
    return {**file_environment, **process_environment}


@dataclass(frozen=True, slots=True)
class DeepgramSettings:
    """Server-only credentials for the Deepgram realtime API."""

    api_key: str = field(repr=False)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DeepgramSettings:
        if environment is None:
            environment = load_backend_environment()

        api_key = environment.get("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "Missing required Deepgram configuration: DEEPGRAM_API_KEY"
            )
        return cls(api_key=api_key)


@dataclass(frozen=True, slots=True)
class AzureSettings:
    endpoint: str
    api_key: str = field(repr=False)
    deployment: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> AzureSettings:
        if environment is None:
            environment = load_backend_environment()

        variable_names = (
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_DEPLOYMENT",
        )
        values = {name: environment.get(name, "").strip() for name in variable_names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            joined_names = ", ".join(missing)
            raise ConfigurationError(
                f"Missing required Azure configuration: {joined_names}"
            )

        endpoint = _normalize_endpoint(values["AZURE_OPENAI_ENDPOINT"])
        return cls(
            endpoint=endpoint,
            api_key=values["AZURE_OPENAI_API_KEY"],
            deployment=values["AZURE_OPENAI_DEPLOYMENT"],
        )

    @property
    def websocket_url(self) -> str:
        parsed = urlsplit(self.endpoint)
        return urlunsplit(
            (
                "wss",
                parsed.netloc,
                "/openai/v1/realtime",
                "intent=transcription",
                "",
            )
        )


def _normalize_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/", "/openai/v1", "/openai/v1/")
    ):
        raise ConfigurationError(
            "AZURE_OPENAI_ENDPOINT must be an HTTPS resource origin or Azure "
            "OpenAI /openai/v1 base URL without credentials, query parameters, "
            "or a fragment"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
