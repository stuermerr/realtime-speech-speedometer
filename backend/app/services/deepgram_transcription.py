"""Reusable Deepgram realtime transport and Results normalization."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, Self, TypeGuard, cast
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.core.config import DeepgramSettings
from app.services.wpm import RecognizedWord


DEEPGRAM_WEBSOCKET_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_LANGUAGE = "de"
DEEPGRAM_SAMPLE_RATE = 24_000
DEEPGRAM_CHANNELS = 1
DEEPGRAM_ENDPOINTING_MILLISECONDS = 600
DEEPGRAM_UTTERANCE_END_MILLISECONDS = 1_000
DEEPGRAM_QUERY_PARAMETERS: Mapping[str, str] = MappingProxyType(
    {
        "model": DEEPGRAM_MODEL,
        "language": DEEPGRAM_LANGUAGE,
        "encoding": "linear16",
        "sample_rate": str(DEEPGRAM_SAMPLE_RATE),
        "channels": str(DEEPGRAM_CHANNELS),
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "vad_events": "true",
        "endpointing": str(DEEPGRAM_ENDPOINTING_MILLISECONDS),
        "utterance_end_ms": str(DEEPGRAM_UTTERANCE_END_MILLISECONDS),
    }
)


class DeepgramError(RuntimeError):
    """Base class for secret-safe Deepgram failures."""


class DeepgramConnectionError(DeepgramError):
    """Raised when the Deepgram realtime connection cannot be used."""


class DeepgramProtocolError(DeepgramError):
    """Raised when a Deepgram event violates the required protocol shape."""


class DeepgramServiceError(DeepgramError):
    """Raised when Deepgram reports a provider-side failure."""


@dataclass(frozen=True, slots=True)
class ParsedDeepgramResult:
    """One complete provider hypothesis normalized to application words."""

    is_final: bool
    words: tuple[RecognizedWord, ...]


class DeepgramConnection(Protocol):
    """Minimal WebSocket behavior required by the application service."""

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def send(self, message: str | bytes) -> None: ...

    async def close(self) -> None: ...


class DeepgramConnector(Protocol):
    """Injectable connector seam used by the direct WebSocket transport."""

    def __call__(
        self, url: str, *, additional_headers: Mapping[str, str]
    ) -> Awaitable[DeepgramConnection]: ...


class DeepgramTranscriptionSession:
    """One reusable Deepgram WebSocket for one transcription session."""

    def __init__(
        self,
        settings: DeepgramSettings,
        *,
        connector: DeepgramConnector | None = None,
    ) -> None:
        self._settings = settings
        self._connector = connector
        self._connection: DeepgramConnection | None = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def open(self) -> None:
        parameters = urlencode(DEEPGRAM_QUERY_PARAMETERS)
        url = f"{DEEPGRAM_WEBSOCKET_URL}?{parameters}"
        headers = {"Authorization": f"Token {self._settings.api_key}"}
        try:
            connection: DeepgramConnection
            if self._connector is None:
                connection = cast(
                    DeepgramConnection,
                    await connect(url, additional_headers=headers),
                )
            else:
                connection = await self._connector(
                    url, additional_headers=headers
                )
            self._connection = connection
        except Exception as error:
            raise DeepgramConnectionError(_safe_connection_error(error)) from error

    async def send_audio(self, audio: bytes) -> None:
        try:
            await self._require_connection().send(audio)
        except DeepgramError:
            raise
        except Exception as error:
            raise DeepgramConnectionError(
                "Could not send audio to Deepgram"
            ) from error

    async def close_stream(self) -> None:
        try:
            await self._require_connection().send(
                json.dumps({"type": "CloseStream"})
            )
        except DeepgramError:
            raise
        except Exception as error:
            raise DeepgramConnectionError(
                "Could not close the Deepgram audio stream"
            ) from error

    async def events(self) -> AsyncIterator[ParsedDeepgramResult]:
        async for payload in self.provider_events():
            result = parse_deepgram_event(payload)
            if result is not None:
                yield result

    async def provider_events(self) -> AsyncIterator[Mapping[str, object]]:
        """Receive validated provider events for spike observation tooling."""
        connection = self._require_connection()
        try:
            async for message in connection:
                try:
                    payload = json.loads(message)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise DeepgramProtocolError(
                        "Deepgram sent malformed JSON"
                    ) from error
                if not isinstance(payload, dict):
                    raise DeepgramProtocolError(
                        "Deepgram sent an event without a valid object"
                    )
                typed_payload = cast(dict[str, object], payload)
                event_type = typed_payload.get("type")
                if not isinstance(event_type, str):
                    raise DeepgramProtocolError(
                        "Deepgram sent an event without a valid type"
                    )
                if event_type == "Error":
                    raise DeepgramServiceError(
                        "Deepgram reported a service error"
                    )
                yield typed_payload
        except DeepgramError:
            raise
        except ConnectionClosed:
            return
        except Exception as error:
            raise DeepgramConnectionError(
                "The Deepgram event stream failed"
            ) from error

    async def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            await connection.close()
        except Exception as error:
            raise DeepgramConnectionError(
                "Could not close the Deepgram connection"
            ) from error

    def _require_connection(self) -> DeepgramConnection:
        if self._connection is None:
            raise DeepgramConnectionError(
                "Deepgram transcription session is not open"
            )
        return self._connection


def parse_deepgram_event(
    payload: Mapping[str, object],
) -> ParsedDeepgramResult | None:
    """Normalize one Results event; ignore additive non-Results events."""
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        raise DeepgramProtocolError("Deepgram sent an event without a valid type")
    if event_type == "Error":
        raise DeepgramServiceError("Deepgram reported a service error")
    if event_type != "Results":
        return None

    is_final = payload.get("is_final")
    channel = payload.get("channel")
    if not isinstance(is_final, bool) or not isinstance(channel, Mapping):
        raise DeepgramProtocolError("Deepgram sent malformed Results")
    alternatives = channel.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        raise DeepgramProtocolError("Deepgram sent malformed Results")
    first_alternative = alternatives[0]
    if not isinstance(first_alternative, Mapping):
        raise DeepgramProtocolError("Deepgram sent malformed Results")
    transcript = first_alternative.get("transcript")
    raw_words = first_alternative.get("words")
    if not isinstance(transcript, str) or not isinstance(raw_words, list):
        raise DeepgramProtocolError("Deepgram sent malformed Results")
    if bool(transcript.strip()) != bool(raw_words):
        raise DeepgramProtocolError(
            "Deepgram Results transcript and timed words are inconsistent"
        )

    words: list[RecognizedWord] = []
    previous_start: float | None = None
    for raw_word in raw_words:
        if not isinstance(raw_word, Mapping):
            raise DeepgramProtocolError("Deepgram sent malformed timed words")
        text = raw_word.get("word")
        start = raw_word.get("start")
        end = raw_word.get("end")
        if not (
            isinstance(text, str)
            and text.strip()
            and _is_finite_number(start)
            and _is_finite_number(end)
            and start >= 0
            and end > start
            and (previous_start is None or start >= previous_start)
        ):
            raise DeepgramProtocolError("Deepgram sent malformed timed words")
        words.append(
            RecognizedWord(
                text=text,
                start_seconds=float(start),
                end_seconds=float(end),
            )
        )
        previous_start = float(start)

    return ParsedDeepgramResult(is_final=is_final, words=tuple(words))


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _safe_connection_error(error: Exception) -> str:
    if isinstance(error, InvalidStatus):
        if error.response.status_code in (401, 403):
            return "Deepgram authentication was rejected"
        return "Deepgram rejected the WebSocket connection"
    return "Could not connect to Deepgram"
