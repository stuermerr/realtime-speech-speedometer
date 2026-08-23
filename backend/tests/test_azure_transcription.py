from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Coroutine, Mapping
from typing import Any, TypeVar

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.core.config import AzureSettings
from spikes.azure_transcription import (
    AzureTranscriptionSession,
    TranscriptionConnectionError,
    TranscriptionProtocolError,
    TranscriptionServiceError,
)


SETTINGS = AzureSettings(
    endpoint="https://speech.example.openai.azure.com",
    api_key="super-secret-key",
    deployment="live-transcribe",
)


class FakeConnection:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming = iter(incoming)
        self.sent: list[dict[str, Any]] = []
        self.close_count = 0

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return next(self.incoming)

    async def close(self) -> None:
        self.close_count += 1


class FakeConnector:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.url: str | None = None
        self.headers: dict[str, str] | None = None

    def __call__(
        self, url: str, *, additional_headers: Mapping[str, str]
    ) -> Awaitable[FakeConnection]:
        self.url = url
        self.headers = dict(additional_headers)

        async def connect() -> FakeConnection:
            return self.connection

        return connect()


class FailingConnector:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(
        self, url: str, *, additional_headers: Mapping[str, str]
    ) -> Awaitable[FakeConnection]:
        async def connect() -> FakeConnection:
            raise self.error

        return connect()


Result = TypeVar("Result")


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


def configured_connection(*following_events: dict[str, Any]) -> FakeConnection:
    events = [
        {"type": "session.created", "event_id": "created-1"},
        {"type": "session.updated", "event_id": "updated-1"},
        *following_events,
    ]
    return FakeConnection([json.dumps(event) for event in events])


def test_session_connects_and_configures_transcription_without_exposing_key() -> None:
    async def scenario() -> tuple[FakeConnector, FakeConnection, str]:
        connection = configured_connection()
        connector = FakeConnector(connection)
        session = AzureTranscriptionSession(SETTINGS, connector=connector)

        await session.open()
        return connector, connection, repr(session)

    connector, connection, representation = run(scenario())

    assert connector.url == (
        "wss://speech.example.openai.azure.com/openai/v1/realtime"
        "?intent=transcription"
    )
    assert connector.headers == {"api-key": "super-secret-key"}
    assert connection.sent == [
        {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {"model": "live-transcribe"},
                        "turn_detection": None,
                    }
                },
            },
        }
    ]
    assert "super-secret-key" not in representation


def test_session_sends_normalized_audio_as_an_append_event() -> None:
    async def scenario() -> list[dict[str, Any]]:
        connection = configured_connection()
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )
        await session.open()

        await session.send_audio(b"\x00\x01\xfe\xff")
        return connection.sent

    sent = run(scenario())

    assert sent[-1] == {
        "type": "input_audio_buffer.append",
        "audio": "AAH+/w==",
    }


def test_session_commits_the_complete_input_buffer() -> None:
    async def scenario() -> list[dict[str, Any]]:
        connection = configured_connection()
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )
        await session.open()

        await session.commit_audio()
        return connection.sent

    sent = run(scenario())

    assert sent[-1] == {"type": "input_audio_buffer.commit"}


def test_connection_failure_is_reported_without_echoing_credentials() -> None:
    async def scenario() -> None:
        session = AzureTranscriptionSession(
            SETTINGS,
            connector=FailingConnector(
                OSError("socket failed with super-secret-key")
            ),
        )
        await session.open()

    with pytest.raises(TranscriptionConnectionError) as caught:
        run(scenario())

    assert "connect" in str(caught.value).lower()
    assert "super-secret-key" not in str(caught.value)


def test_authentication_handshake_failure_has_an_actionable_safe_message() -> None:
    async def scenario() -> None:
        rejection = InvalidStatus(Response(401, "Unauthorized", Headers()))
        session = AzureTranscriptionSession(
            SETTINGS, connector=FailingConnector(rejection)
        )
        await session.open()

    with pytest.raises(TranscriptionConnectionError) as caught:
        run(scenario())

    assert "authentication" in str(caught.value).lower()


def test_session_yields_a_minimal_secret_safe_event_envelope() -> None:
    async def scenario() -> Any:
        connection = configured_connection(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "event_id": "event-7",
                "item_id": "item-3",
                "delta": "Good morning",
                "logprobs": [{"token": "Good", "logprob": -0.1}],
                "audio_start_ms": 120,
                "internal_provider_field": "do not retain",
                "api_key": "provider-echoed-secret",
            }
        )
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection), clock=lambda: 12.5
        )
        await session.open()

        return await anext(session.events())

    event = run(scenario())

    assert event.received_at_seconds == 12.5
    assert event.type == "conversation.item.input_audio_transcription.delta"
    assert event.fields == {
        "event_id": "event-7",
        "item_id": "item-3",
        "delta": "Good morning",
        "logprobs": [{"token": "Good", "logprob": -0.1}],
        "audio_start_ms": 120,
    }
    assert "raw" not in vars(event) if hasattr(event, "__dict__") else True
    assert "provider-echoed-secret" not in repr(event)


def test_session_preserves_documented_transcription_segment_fields() -> None:
    async def scenario() -> Any:
        connection = configured_connection(
            {
                "type": "conversation.item.input_audio_transcription.segment",
                "event_id": "event-8",
                "item_id": "item-3",
                "content_index": 0,
                "id": "seg-1",
                "text": "Good morning",
                "speaker": "spk-1",
                "start": 1.2,
                "end": 2.4,
                "internal_provider_field": "do not retain",
            }
        )
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )
        await session.open()

        return await anext(session.events())

    event = run(scenario())

    assert event.type == "conversation.item.input_audio_transcription.segment"
    assert event.fields == {
        "event_id": "event-8",
        "item_id": "item-3",
        "content_index": 0,
        "id": "seg-1",
        "text": "Good morning",
        "speaker": "spk-1",
        "start": 1.2,
        "end": 2.4,
    }


def test_raw_event_observer_can_capture_full_payload_for_local_debugging() -> None:
    async def scenario() -> tuple[Any, list[tuple[float, Mapping[str, object]]]]:
        raw_events: list[tuple[float, Mapping[str, object]]] = []
        connection = configured_connection(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "delta": "Good",
                "provider_debug_field": {"sequence": 7},
            }
        )
        session = AzureTranscriptionSession(
            SETTINGS,
            connector=FakeConnector(connection),
            clock=lambda: 4.5,
            raw_event_observer=lambda received_at, payload: raw_events.append(
                (received_at, payload)
            ),
        )
        await session.open()

        return await anext(session.events()), raw_events

    event, raw_events = run(scenario())

    assert event.fields == {"delta": "Good"}
    assert raw_events[-1][0] == 4.5
    assert raw_events[-1][1]["provider_debug_field"] == {"sequence": 7}


def test_malformed_provider_event_stops_with_a_safe_protocol_error() -> None:
    async def scenario() -> None:
        connection = configured_connection()
        connection.incoming = iter(["not-json"])
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )
        await session.open()

        await anext(session.events())

    with pytest.raises(TranscriptionProtocolError, match="malformed JSON"):
        run(scenario())


def test_provider_error_stops_without_echoing_provider_payload() -> None:
    sensitive_message = "bad request super-secret-key"

    async def scenario() -> None:
        connection = configured_connection(
            {
                "type": "error",
                "error": {"code": "invalid_api_key", "message": sensitive_message},
            }
        )
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )
        await session.open()

        await anext(session.events())

    with pytest.raises(TranscriptionServiceError) as caught:
        run(scenario())

    assert "authentication" in str(caught.value).lower()
    assert sensitive_message not in str(caught.value)


def test_session_closes_once_when_context_exits() -> None:
    async def scenario() -> int:
        connection = configured_connection()
        session = AzureTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )

        async with session:
            pass
        await session.close()
        return connection.close_count

    assert run(scenario()) == 1
