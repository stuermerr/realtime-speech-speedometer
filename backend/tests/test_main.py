from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import ConfigurationError
from app.main import app, create_app


def test_health_check_does_not_require_or_expose_azure_settings() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_the_vite_product_build(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<h1>Speech Speedometer</h1><script src="/assets/product.js"></script>',
        encoding="utf-8",
    )
    (assets / "product.js").write_text("const product = true;", encoding="utf-8")
    client = TestClient(create_app(frontend_directory=tmp_path))

    response = client.get("/")
    script = client.get("/assets/product.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Speech Speedometer" in response.text
    assert 'src="/assets/product.js"' in response.text
    assert script.status_code == 200
    assert "const product = true" in script.text


class EndpointProvider:
    def __init__(self, events: list[Mapping[str, object]] | None = None) -> None:
        self.close_stream_called = asyncio.Event()
        self.events = list(events or [])
        self.audio: list[bytes] = []
        self.close_count = 0

    async def __aenter__(self) -> EndpointProvider:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.close_count += 1

    async def send_audio(self, audio: bytes) -> None:
        self.audio.append(audio)

    async def close_stream(self) -> None:
        self.close_stream_called.set()

    async def provider_events(self) -> AsyncIterator[Mapping[str, object]]:
        await self.close_stream_called.wait()
        for event in self.events:
            yield event
        yield {"type": "Metadata", "request_id": "safe"}


def test_live_websocket_owns_a_fresh_provider_for_each_session() -> None:
    providers: list[EndpointProvider] = []

    def provider_factory() -> EndpointProvider:
        provider = EndpointProvider(
            [
                {
                    "type": "Results",
                    "is_final": True,
                    "channel": {
                        "alternatives": [
                            {
                                "transcript": "fresh",
                                "words": [{"word": "fresh", "start": 0.0, "end": 0.5}],
                            }
                        ]
                    },
                }
            ]
        )
        providers.append(provider)
        return provider

    client = TestClient(create_app(provider_factory=provider_factory))

    for audio in (b"first-webm", b"second-webm"):
        with client.websocket_connect("/ws/live") as websocket:
            websocket.send_bytes(audio)
            websocket.send_json({"type": "stop"})
            assert websocket.receive_json()["word_count"] == 1
            assert websocket.receive_json() == {
                "type": "summary",
                "average_speaking_pace": None,
                "finalized_words": 1,
                "active_speaking_seconds": 0.5,
                "presentation_duration_seconds": 0.5,
                "segments": [
                    {
                        "text": "fresh",
                        "average_speaking_pace": None,
                        "pace_status": None,
                    }
                ],
            }
            assert websocket.receive_json() == {"type": "stopped", "reason": "user"}

    assert len(providers) == 2
    assert [provider.audio for provider in providers] == [
        [b"first-webm"],
        [b"second-webm"],
    ]
    assert [provider.close_count for provider in providers] == [1, 1]


def test_live_wpm_debug_environment_enables_session_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_WPM_DEBUG", "true")
    output = io.StringIO()
    uvicorn_handler = logging.StreamHandler(output)
    uvicorn_logger = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(uvicorn_logger, "handlers", [uvicorn_handler])
    monkeypatch.setattr(uvicorn_logger, "level", logging.INFO)
    monkeypatch.setattr(uvicorn_logger, "propagate", False)
    client = TestClient(create_app(provider_factory=EndpointProvider))

    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_json({"type": "stop"})
        assert websocket.receive_json()["type"] == "summary"
        assert websocket.receive_json() == {"type": "stopped", "reason": "user"}

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert ("session", "started") in {
        (record["stage"], record["event"]) for record in records
    }


def test_live_wpm_environment_configures_live_without_changing_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_WPM_WINDOW_SECONDS", "2")
    monkeypatch.setenv("LIVE_WPM_MINIMUM_ACTIVE_SECONDS", "1")
    provider = EndpointProvider(
        [
            {
                "type": "Results",
                "is_final": True,
                "channel": {
                    "alternatives": [
                        {
                            "transcript": "one two three",
                            "words": [
                                {"word": "one", "start": 0.0, "end": 0.5},
                                {"word": "two", "start": 0.5, "end": 1.0},
                                {"word": "three", "start": 1.0, "end": 1.5},
                            ],
                        }
                    ]
                },
            }
        ]
    )
    client = TestClient(create_app(provider_factory=lambda: provider))

    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_json({"type": "stop"})
        measurement = websocket.receive_json()
        summary = websocket.receive_json()

    assert measurement["wpm"] == 120.0
    assert summary["average_speaking_pace"] is None


def test_missing_server_configuration_is_reported_without_details() -> None:
    def unavailable_provider() -> EndpointProvider:
        raise ConfigurationError("DEEPGRAM_API_KEY=provider-secret")

    client = TestClient(create_app(provider_factory=unavailable_provider))

    with client.websocket_connect("/ws/live") as websocket:
        message = websocket.receive_json()

    assert message == {
        "type": "error",
        "message": "Live transcription is not configured",
    }
    assert "provider-secret" not in str(message)
