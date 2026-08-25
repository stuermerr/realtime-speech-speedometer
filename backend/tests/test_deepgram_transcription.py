from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Coroutine, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from websockets.exceptions import ConnectionClosedError

from app.core.config import DeepgramSettings
from app.services.deepgram_transcription import (
    DEEPGRAM_WEBSOCKET_URL,
    DeepgramAudioMode,
    DeepgramConnectionError,
    DeepgramProtocolError,
    DeepgramServiceError,
    DeepgramTranscriptionSession,
    ParsedDeepgramResult,
    parse_deepgram_event,
)
from app.services.wpm import RecognizedWord


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "deepgram_realtime_results.json"
)
SETTINGS = DeepgramSettings(api_key="deepgram-secret")
Result = TypeVar("Result")


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


class FakeConnection:
    def __init__(self, incoming: Sequence[str | bytes] | None = None) -> None:
        self.incoming = list(incoming or [])
        self.sent: list[str | bytes] = []
        self.close_count = 0

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        async def messages() -> AsyncIterator[str | bytes]:
            for message in self.incoming:
                yield message

        return messages()

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.close_count += 1


class AbnormallyClosingConnection(FakeConnection):
    def __aiter__(self) -> AsyncIterator[str | bytes]:
        async def messages() -> AsyncIterator[str | bytes]:
            if False:
                yield ""
            raise ConnectionClosedError(None, None)

        return messages()


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

        async def connection() -> FakeConnection:
            return self.connection

        return connection()


class FailingConnector:
    def __call__(
        self, url: str, *, additional_headers: Mapping[str, str]
    ) -> Awaitable[FakeConnection]:
        async def connection() -> FakeConnection:
            raise OSError("socket failed with deepgram-secret")

        return connection()


def test_session_opens_proven_nova_3_configuration_without_exposing_key() -> None:
    async def scenario() -> tuple[FakeConnector, str]:
        connector = FakeConnector(FakeConnection())
        session = DeepgramTranscriptionSession(SETTINGS, connector=connector)
        await session.open()
        return connector, repr(session)

    connector, representation = run(scenario())

    assert connector.url == (
        f"{DEEPGRAM_WEBSOCKET_URL}?model=nova-3&language=de&encoding=linear16"
        "&sample_rate=24000&channels=1&interim_results=true&punctuate=true"
        "&smart_format=true&vad_events=true&endpointing=600&utterance_end_ms=1000"
    )
    assert connector.headers == {"Authorization": "Token deepgram-secret"}
    assert "deepgram-secret" not in representation


def test_session_opens_containerized_webm_without_raw_audio_parameters() -> None:
    async def scenario() -> FakeConnector:
        connector = FakeConnector(FakeConnection())
        session = DeepgramTranscriptionSession(
            SETTINGS,
            audio_mode=DeepgramAudioMode.WEBM_OPUS,
            connector=connector,
        )
        await session.open()
        return connector

    connector = run(scenario())

    assert connector.url == (
        f"{DEEPGRAM_WEBSOCKET_URL}?model=nova-3&language=de"
        "&interim_results=true&punctuate=true&smart_format=true"
        "&vad_events=true&endpointing=600&utterance_end_ms=1000"
    )
    assert "encoding=" not in connector.url
    assert "sample_rate=" not in connector.url
    assert "channels=" not in connector.url


def test_session_sends_audio_closes_stream_and_closes_connection() -> None:
    async def scenario() -> FakeConnection:
        connection = FakeConnection()
        session = DeepgramTranscriptionSession(
            SETTINGS, connector=FakeConnector(connection)
        )
        async with session:
            await session.send_audio(b"\x00\x01")
            await session.close_stream()
        return connection

    connection = run(scenario())

    assert connection.sent == [b"\x00\x01", '{"type": "CloseStream"}']
    assert connection.close_count == 1


def test_session_yields_only_atomically_parsed_results() -> None:
    payloads = cast(list[dict[str, object]], json.loads(FIXTURE_PATH.read_text()))
    incoming = [
        json.dumps({"type": "Metadata", "request_id": "safe"}),
        json.dumps(payloads[0]),
        json.dumps({"type": "FutureEvent", "new_field": True}),
        json.dumps(payloads[-1]),
    ]

    async def scenario() -> list[ParsedDeepgramResult]:
        session = DeepgramTranscriptionSession(
            SETTINGS, connector=FakeConnector(FakeConnection(incoming))
        )
        await session.open()
        return [result async for result in session.events()]

    results = run(scenario())

    assert [result.is_final for result in results] == [False, True]
    assert [len(result.words) for result in results] == [2, 3]


def test_malformed_wire_event_raises_typed_protocol_error() -> None:
    async def scenario() -> None:
        session = DeepgramTranscriptionSession(
            SETTINGS, connector=FakeConnector(FakeConnection(["not-json"]))
        )
        await session.open()
        await anext(session.events())

    with pytest.raises(DeepgramProtocolError, match="malformed JSON"):
        run(scenario())


def test_connection_failure_is_typed_and_secret_safe() -> None:
    async def scenario() -> None:
        session = DeepgramTranscriptionSession(SETTINGS, connector=FailingConnector())
        await session.open()

    with pytest.raises(DeepgramConnectionError) as caught:
        run(scenario())

    assert "connect" in str(caught.value).lower()
    assert "deepgram-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_abnormal_provider_close_is_a_connection_failure() -> None:
    async def scenario() -> None:
        session = DeepgramTranscriptionSession(
            SETTINGS,
            connector=FakeConnector(AbnormallyClosingConnection()),
        )
        await session.open()
        _ = [event async for event in session.provider_events()]

    with pytest.raises(DeepgramConnectionError, match="event stream failed"):
        run(scenario())


def test_sanitized_results_preserve_atomic_interim_revision_and_finalization() -> None:
    payloads = cast(list[dict[str, object]], json.loads(FIXTURE_PATH.read_text()))

    results = [parse_deepgram_event(payload) for payload in payloads]

    assert results == [
        ParsedDeepgramResult(
            is_final=False,
            text="Guten Morgen",
            words=(
                RecognizedWord("guten", 0.16, 0.48),
                RecognizedWord("morgen", 0.50, 0.91),
            ),
        ),
        ParsedDeepgramResult(
            is_final=False,
            text="Guten Morgen zusammen",
            words=(
                RecognizedWord("guten", 0.16, 0.48),
                RecognizedWord("morgen", 0.50, 0.91),
                RecognizedWord("zusammen", 0.94, 1.42),
            ),
        ),
        ParsedDeepgramResult(
            is_final=False,
            text="Guten Morgen miteinander",
            words=(
                RecognizedWord("guten", 0.16, 0.48),
                RecognizedWord("morgen", 0.50, 0.90),
                RecognizedWord("miteinander", 0.92, 1.51),
            ),
        ),
        ParsedDeepgramResult(
            is_final=False,
            text="Guten Morgen",
            words=(
                RecognizedWord("guten", 0.16, 0.48),
                RecognizedWord("morgen", 0.50, 0.90),
            ),
        ),
        ParsedDeepgramResult(is_final=False, text="", words=()),
        ParsedDeepgramResult(
            is_final=True,
            text="Guten Morgen miteinander",
            words=(
                RecognizedWord("guten", 0.16, 0.48),
                RecognizedWord("morgen", 0.50, 0.90),
                RecognizedWord("miteinander", 0.92, 1.51),
            ),
        ),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "Results", "is_final": "false", "channel": {}},
        {"type": "Results", "is_final": False, "channel": {}},
        {
            "type": "Results",
            "is_final": False,
            "channel": {"alternatives": []},
        },
        {
            "type": "Results",
            "is_final": False,
            "channel": {
                "alternatives": [{"transcript": "spoken", "words": []}]
            },
        },
        {
            "type": "Results",
            "is_final": False,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "spoken",
                        "words": [
                            {"word": "spoken", "start": 1.0, "end": "1.2"}
                        ],
                    }
                ]
            },
        },
        {
            "type": "Results",
            "is_final": False,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "spoken",
                        "words": [
                            {"word": "spoken", "start": False, "end": 1.2}
                        ],
                    }
                ]
            },
        },
    ],
)
def test_malformed_results_fail_without_partial_output(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DeepgramProtocolError):
        parse_deepgram_event(payload)


def test_non_results_events_are_additive_and_nonfatal() -> None:
    assert parse_deepgram_event({"type": "SpeechStarted", "timestamp": 1.2}) is None
    assert parse_deepgram_event({"type": "FutureEvent", "new_field": True}) is None


def test_only_the_first_alternative_is_part_of_the_parser_contract() -> None:
    result = parse_deepgram_event(
        {
            "type": "Results",
            "is_final": False,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "spoken",
                        "words": [
                            {"word": "spoken", "start": 0.0, "end": 0.5}
                        ],
                    },
                    {"provider_future_shape": object()},
                ]
            },
        }
    )

    assert result == ParsedDeepgramResult(
        is_final=False,
        text="spoken",
        words=(RecognizedWord("spoken", 0.0, 0.5),),
    )


def test_provider_error_event_is_a_service_failure_without_echoing_payload() -> None:
    secret = "provider-echoed-secret"

    with pytest.raises(DeepgramServiceError) as caught:
        parse_deepgram_event(
            {"type": "Error", "description": f"bad request: {secret}"}
        )

    assert secret not in str(caught.value)
