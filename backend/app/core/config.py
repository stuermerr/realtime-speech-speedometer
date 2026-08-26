from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values


BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]
BACKEND_ENV_PATH = BACKEND_DIRECTORY / ".env"
LiveWpmMode = Literal["single", "dual"]
LIVE_WPM_WINDOW_SECONDS = 5.0
LIVE_WPM_MINIMUM_ACTIVE_SECONDS = 1.0
LIVE_WPM_MODE: LiveWpmMode = "dual"
LIVE_WPM_SHORT_WINDOW_SECONDS = 2.0
LIVE_WPM_LONG_WINDOW_SECONDS = 10.0
LIVE_WPM_SHORT_WEIGHT = 0.2


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
class LiveWpmDebugSettings:
    """Opt-in diagnostics for local browser-to-WPM sessions."""

    enabled: bool = False

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> LiveWpmDebugSettings:
        if environment is None:
            environment = load_backend_environment()

        value = environment.get("LIVE_WPM_DEBUG", "false").strip().lower()
        if value not in {"true", "false"}:
            raise ConfigurationError("LIVE_WPM_DEBUG must be true or false")
        return cls(enabled=value == "true")


@dataclass(frozen=True, slots=True)
class LiveWpmSettings:
    """Validated startup configuration for live active-speech pace."""

    mode: LiveWpmMode = LIVE_WPM_MODE
    window_seconds: float = LIVE_WPM_WINDOW_SECONDS
    minimum_active_seconds: float = LIVE_WPM_MINIMUM_ACTIVE_SECONDS
    short_window_seconds: float = LIVE_WPM_SHORT_WINDOW_SECONDS
    long_window_seconds: float = LIVE_WPM_LONG_WINDOW_SECONDS
    short_weight: float = LIVE_WPM_SHORT_WEIGHT

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> LiveWpmSettings:
        if environment is None:
            environment = load_backend_environment()
        mode = _live_wpm_mode(environment)
        window_seconds = _live_wpm_seconds(
            environment, "LIVE_WPM_WINDOW_SECONDS", LIVE_WPM_WINDOW_SECONDS
        )
        minimum_active_seconds = _live_wpm_seconds(
            environment,
            "LIVE_WPM_MINIMUM_ACTIVE_SECONDS",
            LIVE_WPM_MINIMUM_ACTIVE_SECONDS,
        )
        short_window_seconds = _live_wpm_seconds(
            environment,
            "LIVE_WPM_SHORT_WINDOW_SECONDS",
            LIVE_WPM_SHORT_WINDOW_SECONDS,
        )
        long_window_seconds = _live_wpm_seconds(
            environment,
            "LIVE_WPM_LONG_WINDOW_SECONDS",
            LIVE_WPM_LONG_WINDOW_SECONDS,
        )
        short_weight = _live_wpm_weight(environment)
        if short_window_seconds > long_window_seconds:
            raise ConfigurationError(
                "LIVE_WPM_SHORT_WINDOW_SECONDS must not exceed "
                "LIVE_WPM_LONG_WINDOW_SECONDS"
            )
        availability_window = (
            window_seconds if mode == "single" else short_window_seconds
        )
        if minimum_active_seconds > availability_window:
            raise ConfigurationError(
                "LIVE_WPM_MINIMUM_ACTIVE_SECONDS must not exceed "
                + (
                    "LIVE_WPM_WINDOW_SECONDS"
                    if mode == "single"
                    else "LIVE_WPM_SHORT_WINDOW_SECONDS"
                )
            )
        return cls(
            mode=mode,
            window_seconds=window_seconds,
            minimum_active_seconds=minimum_active_seconds,
            short_window_seconds=short_window_seconds,
            long_window_seconds=long_window_seconds,
            short_weight=short_weight,
        )


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


def _live_wpm_seconds(
    environment: Mapping[str, str], variable_name: str, default: float
) -> float:
    if variable_name not in environment:
        return default
    try:
        value = float(environment[variable_name].strip())
    except ValueError:
        raise ConfigurationError(
            f"{variable_name} must be a finite positive number"
        ) from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{variable_name} must be a finite positive number")
    return value


def _live_wpm_mode(environment: Mapping[str, str]) -> LiveWpmMode:
    mode = environment.get("LIVE_WPM_MODE", LIVE_WPM_MODE).strip().lower()
    if mode == "single":
        return "single"
    if mode == "dual":
        return "dual"
    raise ConfigurationError("LIVE_WPM_MODE must be single or dual")


def _live_wpm_weight(environment: Mapping[str, str]) -> float:
    variable_name = "LIVE_WPM_SHORT_WEIGHT"
    if variable_name not in environment:
        return LIVE_WPM_SHORT_WEIGHT
    try:
        value = float(environment[variable_name].strip())
    except ValueError:
        raise ConfigurationError(
            f"{variable_name} must be a finite number between zero and one"
        ) from None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ConfigurationError(
            f"{variable_name} must be a finite number between zero and one"
        )
    return value
