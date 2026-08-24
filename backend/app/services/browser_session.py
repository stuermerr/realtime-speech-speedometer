"""One browser microphone session coordinated with one Deepgram stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from enum import Enum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.deepgram_transcription import DeepgramError
from app.services.live_wpm import LiveWpmPipeline
from app.services.wpm import WpmMeasurement


class BrowserSessionProtocolError(RuntimeError):
    """Raised when either peer cannot complete the browser session protocol."""


class _BrowserDisconnected(RuntimeError):
    pass


class BrowserWebSocket(Protocol):
    """Minimal accepted FastAPI WebSocket interface used by a live session."""

    async def receive(self) -> Mapping[str, object]: ...

    async def send_json(self, data: object) -> None: ...


class BrowserDeepgramSession(Protocol):
    """Deepgram operations owned by one browser WebSocket."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    async def send_audio(self, audio: bytes) -> None: ...

    async def close_stream(self) -> None: ...

    def provider_events(self) -> AsyncIterator[Mapping[str, object]]: ...


class _StopCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["stop"]


class _MeasurementMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["measurement"] = "measurement"
    wpm: float | None
    word_count: int
    active_speech_seconds: float
    audio_start_seconds: float | None
    audio_end_seconds: float | None

    @classmethod
    def from_measurement(cls, measurement: WpmMeasurement) -> _MeasurementMessage:
        return cls(
            wpm=measurement.wpm,
            word_count=measurement.word_count,
            active_speech_seconds=measurement.active_speech_seconds,
            audio_start_seconds=measurement.audio_start_seconds,
            audio_end_seconds=measurement.audio_end_seconds,
        )


class _StoppedMessage(BaseModel):
    type: Literal["stopped"] = "stopped"


class _ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


class _BrowserCompletion(Enum):
    STOP = "stop"


class BrowserLiveWpmSession:
    """Relay and measure one browser stream without an internal audio queue."""

    def __init__(
        self,
        browser: BrowserWebSocket,
        provider: BrowserDeepgramSession,
        *,
        drain_timeout_seconds: float = 5.0,
    ) -> None:
        if drain_timeout_seconds <= 0:
            raise ValueError("Provider drain timeout must be positive")
        self._browser = browser
        self._provider = provider
        self._drain_timeout_seconds = drain_timeout_seconds
        self._pipeline = LiveWpmPipeline()

    async def run(self) -> None:
        browser_task: asyncio.Task[_BrowserCompletion] | None = None
        provider_task: asyncio.Task[bool] | None = None
        try:
            async with self._provider:
                browser_task = asyncio.create_task(self._forward_browser())
                provider_task = asyncio.create_task(self._forward_provider())
                done, _ = await asyncio.wait(
                    (browser_task, provider_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if browser_task in done:
                    completion = await browser_task
                    if completion is _BrowserCompletion.STOP:
                        metadata_seen = await self._drain_provider(provider_task)
                        if not metadata_seen:
                            raise BrowserSessionProtocolError(
                                "Deepgram closed without final Metadata"
                            )
                        await self._send(_StoppedMessage())
                        return

                await provider_task
                raise BrowserSessionProtocolError(
                    "Deepgram closed before the browser stopped"
                )
        except _BrowserDisconnected:
            return
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._send_error("Transcription did not finish in time")
        except BrowserSessionProtocolError:
            await self._send_error("Session protocol error")
        except DeepgramError:
            await self._send_error("Live transcription failed")
        except Exception:
            await self._send_error("Live session failed")
        finally:
            await _cancel(browser_task)
            await _cancel(provider_task)

    async def _forward_browser(self) -> _BrowserCompletion:
        while True:
            message = await self._browser.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                raise _BrowserDisconnected
            if message_type != "websocket.receive":
                raise BrowserSessionProtocolError("Unknown browser message")

            audio = message.get("bytes")
            text = message.get("text")
            if isinstance(audio, bytes):
                if not audio or text is not None:
                    raise BrowserSessionProtocolError("Invalid audio frame")
                await self._provider.send_audio(audio)
                continue
            if isinstance(text, str) and audio is None:
                _parse_stop_command(text)
                await self._provider.close_stream()
                return _BrowserCompletion.STOP
            raise BrowserSessionProtocolError("Invalid browser message")

    async def _forward_provider(self) -> bool:
        metadata_seen = False
        async for event in self._provider.provider_events():
            if event.get("type") == "Metadata":
                metadata_seen = True
            measurement = self._pipeline.process_event(event)
            if measurement is not None:
                await self._send(_MeasurementMessage.from_measurement(measurement))
        return metadata_seen

    async def _drain_provider(self, provider_task: asyncio.Task[bool]) -> bool:
        async with asyncio.timeout(self._drain_timeout_seconds):
            return await provider_task

    async def _send(self, message: BaseModel) -> None:
        await self._browser.send_json(message.model_dump(mode="json"))

    async def _send_error(self, message: str) -> None:
        try:
            await self._send(_ErrorMessage(message=message))
        except Exception:
            pass


def _parse_stop_command(message: str) -> None:
    try:
        payload = json.loads(message)
        _StopCommand.model_validate(payload)
    except (json.JSONDecodeError, ValidationError):
        raise BrowserSessionProtocolError("Invalid browser control") from None


async def _cancel(task: asyncio.Task[object] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
