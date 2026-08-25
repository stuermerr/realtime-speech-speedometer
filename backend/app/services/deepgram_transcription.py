"""Reusable Deepgram realtime transport and Results normalization."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self, TypeVar, cast
from urllib.parse import urlencode

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, InvalidStatus

from app.core.config import DeepgramSettings
from app.services.wpm import RecognizedWord


DEEPGRAM_WEBSOCKET_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_LANGUAGE = "de"
DEEPGRAM_ENCODING = "linear16"
DEEPGRAM_SAMPLE_RATE = 24_000
DEEPGRAM_CHANNELS = 1
DEEPGRAM_ENDPOINTING_MILLISECONDS = 600
DEEPGRAM_UTTERANCE_END_MILLISECONDS = 1_000
DEEPGRAM_QUERY_PARAMETERS: Mapping[str, str] = MappingProxyType(
    {
        "model": DEEPGRAM_MODEL,
        "language": DEEPGRAM_LANGUAGE,
        "encoding": DEEPGRAM_ENCODING,
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


class DeepgramAudioMode(StrEnum):
    """Audio framing modes supported by the direct Deepgram transport."""

    LINEAR16_24KHZ_MONO = "linear16-24khz-mono"
    WEBM_OPUS = "webm-opus"


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
    text: str
    words: tuple[RecognizedWord, ...]


class _DeepgramEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    type: StrictStr


class _DeepgramChannelEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    alternatives: tuple[Any, ...] = Field(min_length=1)


class _DeepgramResultsEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    type: Literal["Results"]
    is_final: StrictBool
    channel: _DeepgramChannelEnvelope


class _DeepgramWordPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    word: StrictStr
    start: StrictInt | StrictFloat
    end: StrictInt | StrictFloat

    @model_validator(mode="after")
    def validate_word(self) -> Self:
        if (
            not self.word.strip()
            or not math.isfinite(self.start)
            or not math.isfinite(self.end)
            or self.start < 0
            or self.end <= self.start
        ):
            raise ValueError("invalid timed word")
        return self


class _DeepgramAlternativePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    transcript: StrictStr
    words: tuple[_DeepgramWordPayload, ...]

    @model_validator(mode="after")
    def validate_hypothesis(self) -> Self:
        if bool(self.transcript.strip()) != bool(self.words):
            raise ValueError("inconsistent transcript and words")
        if any(
            current.start < previous.start
            for previous, current in zip(self.words, self.words[1:])
        ):
            raise ValueError("words are not chronological")
        return self


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
        audio_mode: DeepgramAudioMode = DeepgramAudioMode.LINEAR16_24KHZ_MONO,
        connector: DeepgramConnector | None = None,
    ) -> None:
        self._settings = settings
        self._audio_mode = audio_mode
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
        query_parameters = dict(DEEPGRAM_QUERY_PARAMETERS)
        if self._audio_mode is DeepgramAudioMode.WEBM_OPUS:
            for raw_parameter in ("encoding", "sample_rate", "channels"):
                del query_parameters[raw_parameter]
        parameters = urlencode(query_parameters)
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
        except Exception as error:
            safe_error = _safe_connection_error(error)
        else:
            self._connection = connection
            return
        raise DeepgramConnectionError(safe_error)

    async def send_audio(self, audio: bytes) -> None:
        connection = self._require_connection()
        try:
            await connection.send(audio)
        except Exception:
            pass
        else:
            return
        raise DeepgramConnectionError("Could not send audio to Deepgram")

    async def close_stream(self) -> None:
        connection = self._require_connection()
        try:
            await connection.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        else:
            return
        raise DeepgramConnectionError("Could not close the Deepgram audio stream")

    async def events(self) -> AsyncIterator[ParsedDeepgramResult]:
        async for payload in self.provider_events():
            result = parse_deepgram_event(payload)
            if result is not None:
                yield result

    async def provider_events(self) -> AsyncIterator[Mapping[str, object]]:
        """Receive validated provider events for spike observation tooling."""
        connection = self._require_connection()
        stream_failed = False
        try:
            async for message in connection:
                typed_payload = _decode_provider_message(message)
                envelope = _validate_payload(
                    _DeepgramEventEnvelope,
                    typed_payload,
                    "Deepgram sent an event without a valid type",
                )
                if envelope.type == "Error":
                    raise DeepgramServiceError(
                        "Deepgram reported a service error"
                    )
                yield MappingProxyType(typed_payload)
        except DeepgramError:
            raise
        except ConnectionClosedOK:
            return
        except Exception:
            stream_failed = True
        if stream_failed:
            raise DeepgramConnectionError("The Deepgram event stream failed")

    async def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            await connection.close()
        except Exception:
            pass
        else:
            return
        raise DeepgramConnectionError("Could not close the Deepgram connection")

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
    envelope = _validate_payload(
        _DeepgramEventEnvelope,
        payload,
        "Deepgram sent an event without a valid type",
    )
    if envelope.type == "Error":
        raise DeepgramServiceError("Deepgram reported a service error")
    if envelope.type != "Results":
        return None

    result = _validate_payload(
        _DeepgramResultsEnvelope,
        payload,
        "Deepgram sent malformed Results",
    )
    alternative = _validate_payload(
        _DeepgramAlternativePayload,
        result.channel.alternatives[0],
        "Deepgram sent malformed Results",
    )
    return ParsedDeepgramResult(
        is_final=result.is_final,
        text=alternative.transcript,
        words=tuple(
            RecognizedWord(
                text=word.word,
                start_seconds=float(word.start),
                end_seconds=float(word.end),
            )
            for word in alternative.words
        )
    )


Model = TypeVar("Model", bound=BaseModel)


def _validate_payload(
    model: type[Model], payload: object, error_message: str
) -> Model:
    try:
        parsed = model.model_validate(payload)
    except ValidationError:
        pass
    else:
        return parsed
    raise DeepgramProtocolError(error_message)


def _decode_provider_message(message: str | bytes) -> dict[str, object]:
    try:
        payload = json.loads(message)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    else:
        if isinstance(payload, dict):
            return cast(dict[str, object], payload)
        raise DeepgramProtocolError(
            "Deepgram sent an event without a valid object"
        )
    raise DeepgramProtocolError("Deepgram sent malformed JSON")


def _safe_connection_error(error: Exception) -> str:
    if isinstance(error, InvalidStatus):
        if error.response.status_code in (401, 403):
            return "Deepgram authentication was rejected"
        return "Deepgram rejected the WebSocket connection"
    return "Could not connect to Deepgram"
