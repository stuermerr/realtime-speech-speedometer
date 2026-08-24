from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

from fastapi.testclient import TestClient

from app.core.config import ConfigurationError
from app.main import app, create_app


def test_health_check_does_not_require_or_expose_azure_settings() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_vanilla_microphone_debug_client() -> None:
    client = TestClient(app)

    response = client.get("/")
    script = client.get("/static/app.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Speech Speedometer" in response.text
    assert 'src="/static/app.js"' in response.text
    assert script.status_code == 200
    assert "audio/webm;codecs=opus" in script.text
    assert "MediaRecorder.isTypeSupported" in script.text
    assert "CHUNK_MILLISECONDS = 250" in script.text
    assert "recorder.start(CHUNK_MILLISECONDS)" in script.text


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
                                "words": [
                                    {"word": "fresh", "start": 0.0, "end": 0.5}
                                ],
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
            assert websocket.receive_json() == {"type": "stopped"}

    assert len(providers) == 2
    assert [provider.audio for provider in providers] == [
        [b"first-webm"],
        [b"second-webm"],
    ]
    assert [provider.close_count for provider in providers] == [1, 1]


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
