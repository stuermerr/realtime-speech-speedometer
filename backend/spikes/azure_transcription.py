from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from json import JSONDecodeError
from types import MappingProxyType
from typing import Protocol, Self, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.core.config import AzureSettings
from spikes.provider_event import ProviderEvent


class TranscriptionError(RuntimeError):
    """Base class for secret-safe transcription failures."""


class TranscriptionConnectionError(TranscriptionError):
    """Raised when the Azure realtime connection cannot be used."""


class TranscriptionProtocolError(TranscriptionError):
    """Raised when Azure sends an invalid realtime event."""


class TranscriptionServiceError(TranscriptionError):
    """Raised when Azure reports a provider-side error event."""


class _Connection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


class _Connector(Protocol):
    def __call__(
        self, url: str, *, additional_headers: Mapping[str, str]
    ) -> Awaitable[_Connection]: ...


async def _connect(
    url: str, *, additional_headers: Mapping[str, str]
) -> _Connection:
    return await connect(url, additional_headers=additional_headers)


_EVENT_FIELDS = (
    # Generic event identity and relationships.
    "event_id",
    "item_id",
    "previous_item_id",
    "response_id",
    "content_index",
    "output_index",
    # Incremental and completed transcription.
    "transcript",
    "delta",
    # Potential transcription-segment metadata.
    "id",
    "text",
    "speaker",
    "start",
    "end",
    # Other timing information exposed by some realtime events.
    "audio_start_ms",
    "audio_end_ms",
    # Useful transcription metadata.
    "usage",
    "logprobs",
)


class AzureTranscriptionSession:
    """One Azure realtime connection for one Speech Speedometer session."""

    def __init__(
        self,
        settings: AzureSettings,
        *,
        connector: _Connector = _connect,
        clock: Callable[[], float] = time.monotonic,
        raw_event_observer: (
            Callable[[float, Mapping[str, object]], None] | None
        ) = None,
    ) -> None:
        self._settings = settings
        self._connector = connector
        self._clock = clock
        self._raw_event_observer = raw_event_observer
        self._connection: _Connection | None = None

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
        if self._connection is not None:
            raise TranscriptionConnectionError(
                "Azure transcription session is already open"
            )

        try:
            connection = await self._connector(
                self._settings.websocket_url,
                additional_headers={"api-key": self._settings.api_key},
            )
        except Exception as error:
            raise TranscriptionConnectionError(
                _safe_connection_error_message(error)
            ) from error

        self._connection = connection
        try:
            await connection.send(json.dumps(self._session_update()))
            while True:
                event = await self._receive_payload(connection)
                if event["type"] == "session.updated":
                    return
        except TranscriptionError:
            await self.close()
            raise
        except Exception as error:
            await self.close()
            raise TranscriptionConnectionError(
                "Azure disconnected while configuring transcription"
            ) from error

    async def send_audio(self, audio: bytes) -> None:
        connection = self._require_connection()
        if not audio:
            raise ValueError("An audio chunk cannot be empty")

        message = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(audio).decode("ascii"),
        }
        try:
            await connection.send(json.dumps(message))
        except Exception as error:
            raise TranscriptionConnectionError(
                "Azure disconnected while receiving audio"
            ) from error

    async def commit_audio(self) -> None:
        connection = self._require_connection()
        try:
            await connection.send(
                json.dumps({"type": "input_audio_buffer.commit"})
            )
        except Exception as error:
            raise TranscriptionConnectionError(
                "Azure disconnected while committing audio"
            ) from error

    async def events(self) -> AsyncIterator[ProviderEvent]:
        connection = self._require_connection()
        while True:
            try:
                payload = await self._receive_payload(connection)
            except TranscriptionError:
                raise
            except Exception as error:
                raise TranscriptionConnectionError(
                    "Azure realtime transcription disconnected"
                ) from error

            fields = {
                field: payload[field] for field in _EVENT_FIELDS if field in payload
            }
            yield ProviderEvent(
                received_at_seconds=self._clock(),
                type=cast(str, payload["type"]),
                fields=MappingProxyType(fields),
            )

    async def close(self) -> None:
        connection = self._connection
        if connection is None:
            return
        self._connection = None
        try:
            await connection.close()
        except ConnectionClosed:
            return
        except Exception as error:
            raise TranscriptionConnectionError(
                "Azure realtime transcription could not close cleanly"
            ) from error

    def _require_connection(self) -> _Connection:
        if self._connection is None:
            raise TranscriptionConnectionError(
                "Azure transcription session is not open"
            )
        return self._connection

    def _session_update(self) -> dict[str, object]:
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {"model": self._settings.deployment},
                        "turn_detection": None,
                    }
                },
            },
        }

    async def _receive_payload(
        self, connection: _Connection
    ) -> dict[str, object]:
        raw_message = await connection.recv()
        try:
            payload = json.loads(raw_message)
        except (JSONDecodeError, UnicodeDecodeError) as error:
            raise TranscriptionProtocolError(
                "Azure realtime transcription sent malformed JSON"
            ) from error

        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise TranscriptionProtocolError(
                "Azure realtime transcription sent an event without a valid type"
            )
        typed_payload = cast(dict[str, object], payload)
        if self._raw_event_observer is not None:
            self._raw_event_observer(
                self._clock(), MappingProxyType(typed_payload.copy())
            )
        if typed_payload["type"] == "error":
            raise _safe_service_error(typed_payload)
        return typed_payload


def _safe_service_error(payload: Mapping[str, object]) -> TranscriptionServiceError:
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    normalized_code = code.lower() if isinstance(code, str) else ""
    if "auth" in normalized_code or "api_key" in normalized_code:
        message = "Azure realtime transcription authentication was rejected"
    elif "deployment" in normalized_code or "model" in normalized_code:
        message = "Azure realtime transcription deployment was rejected"
    else:
        message = "Azure realtime transcription reported a service error"
    return TranscriptionServiceError(message)


def _safe_connection_error_message(error: Exception) -> str:
    if isinstance(error, InvalidStatus):
        if error.response.status_code in (401, 403):
            return "Azure realtime transcription authentication was rejected"
        if error.response.status_code == 404:
            return "Azure realtime transcription endpoint or deployment was not found"
        return "Azure realtime transcription rejected the WebSocket connection"
    return "Could not connect to Azure realtime transcription"
