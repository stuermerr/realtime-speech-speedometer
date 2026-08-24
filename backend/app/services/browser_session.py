"""One browser microphone session coordinated with one Deepgram stream."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.deepgram_transcription import DeepgramError, parse_deepgram_event
from app.services.live_wpm import LiveWpmPipeline
from app.services.wpm import WpmMeasurement


_diagnostic_logger = logging.getLogger("speech_speedometer.live_wpm")
DiagnosticValue = str | int | float | bool | None


class LiveWpmDiagnostics:
    """Emit bounded JSON diagnostics for one browser session when enabled."""

    def __init__(
        self,
        *,
        enabled: bool,
        sink: Callable[[str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_id: str | None = None,
    ) -> None:
        self._enabled = enabled
        if enabled and sink is None:
            _diagnostic_logger.setLevel(logging.INFO)
        self._sink = _diagnostic_logger.info if sink is None else sink
        self._clock = clock
        self._started_at = clock()
        self._session_id = uuid.uuid4().hex if session_id is None else session_id

    def record(
        self,
        stage: str,
        event: str,
        **fields: DiagnosticValue,
    ) -> None:
        if not self._enabled:
            return
        record: dict[str, DiagnosticValue] = {
            "session_id": self._session_id,
            "relative_seconds": round(self._clock() - self._started_at, 6),
            "stage": stage,
            "event": event,
            **fields,
        }
        self._sink(json.dumps(record, separators=(",", ":"), sort_keys=True))


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


class BrowserLiveWpmSession:
    """Relay and measure one browser stream without an internal audio queue."""

    def __init__(
        self,
        browser: BrowserWebSocket,
        provider: BrowserDeepgramSession,
        *,
        diagnostics: LiveWpmDiagnostics | None = None,
        drain_timeout_seconds: float = 5.0,
    ) -> None:
        if drain_timeout_seconds <= 0:
            raise ValueError("Provider drain timeout must be positive")
        self._browser = browser
        self._provider = provider
        self._drain_timeout_seconds = drain_timeout_seconds
        self._pipeline = LiveWpmPipeline()
        self._diagnostics = (
            LiveWpmDiagnostics(enabled=False) if diagnostics is None else diagnostics
        )

    async def run(self) -> None:
        browser_task: asyncio.Task[None] | None = None
        provider_task: asyncio.Task[bool] | None = None
        self._diagnostics.record("session", "started")
        try:
            self._diagnostics.record("provider", "opening")
            async with self._provider:
                self._diagnostics.record("provider", "opened")
                browser_task = asyncio.create_task(self._forward_browser())
                provider_task = asyncio.create_task(self._forward_provider())
                done, _ = await asyncio.wait(
                    (browser_task, provider_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if browser_task in done:
                    try:
                        await browser_task
                    except BaseException:
                        await _cancel(provider_task)
                        raise
                    metadata_seen = await self._drain_provider(provider_task)
                    if not metadata_seen:
                        raise BrowserSessionProtocolError(
                            "Deepgram closed without final Metadata"
                        )
                    await self._send(_StoppedMessage())
                    self._diagnostics.record("browser", "stopped_sent")
                    return

                try:
                    await provider_task
                finally:
                    await _cancel(browser_task)
                raise BrowserSessionProtocolError(
                    "Deepgram closed before the browser stopped"
                )
        except _BrowserDisconnected:
            self._diagnostics.record("browser", "disconnected")
            return
        except asyncio.CancelledError:
            self._diagnostics.record("session", "cancelled")
            raise
        except TimeoutError:
            self._diagnostics.record("provider", "drain_timeout")
            await self._send_error("Transcription did not finish in time")
        except BrowserSessionProtocolError:
            self._diagnostics.record("session", "failed", category="protocol")
            await self._send_error("Session protocol error")
        except DeepgramError:
            self._diagnostics.record("session", "failed", category="provider")
            await self._send_error("Live transcription failed")
        except Exception:
            self._diagnostics.record("session", "failed", category="internal")
            await self._send_error("Live session failed")
        finally:
            self._diagnostics.record("session", "cleanup_started")
            await _cancel(browser_task)
            await _cancel(provider_task)
            self._diagnostics.record("session", "cleanup_finished")

    async def _forward_browser(self) -> None:
        chunk_index = 0
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
                chunk_index += 1
                self._diagnostics.record(
                    "browser",
                    "audio_received",
                    chunk_index=chunk_index,
                    byte_size=len(audio),
                )
                await self._provider.send_audio(audio)
                self._diagnostics.record(
                    "provider",
                    "audio_forwarded",
                    chunk_index=chunk_index,
                    byte_size=len(audio),
                )
                continue
            if isinstance(text, str) and audio is None:
                _parse_stop_command(text)
                self._diagnostics.record("browser", "stop_received")
                await self._provider.close_stream()
                self._diagnostics.record("provider", "close_stream_sent")
                return
            raise BrowserSessionProtocolError("Invalid browser message")

    async def _forward_provider(self) -> bool:
        metadata_seen = False
        try:
            async for event in self._provider.provider_events():
                event_type = _safe_provider_event_type(event.get("type"))
                result = parse_deepgram_event(event)
                if result is None:
                    self._diagnostics.record(
                        "provider",
                        "event_received",
                        provider_event_type=event_type,
                    )
                    if event_type == "Metadata":
                        metadata_seen = True
                    continue

                audio_start = result.words[0].start_seconds if result.words else None
                audio_end = result.words[-1].end_seconds if result.words else None
                self._diagnostics.record(
                    "provider",
                    "event_received",
                    provider_event_type=event_type,
                    is_final=result.is_final,
                    word_count=len(result.words),
                    audio_start_seconds=audio_start,
                    audio_end_seconds=audio_end,
                )
                measurement = self._pipeline.process_result(result)
                changed = measurement is not None
                self._diagnostics.record(
                    "pipeline", "timeline_changed", changed=changed
                )
                if measurement is None:
                    self._diagnostics.record(
                        "browser",
                        "measurement_suppressed",
                        reason="unchanged_timeline",
                    )
                    continue
                self._diagnostics.record(
                    "pipeline",
                    "wpm_availability",
                    available=measurement.wpm is not None,
                )
                await self._send(_MeasurementMessage.from_measurement(measurement))
                self._diagnostics.record("browser", "measurement_sent")
        except asyncio.CancelledError:
            self._diagnostics.record("provider", "stream_closed", outcome="cancelled")
            raise
        except BaseException:
            self._diagnostics.record("provider", "stream_closed", outcome="abnormal")
            raise
        self._diagnostics.record("provider", "stream_closed", outcome="normal")
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


def _safe_provider_event_type(value: object) -> str:
    if value in {"Results", "Metadata", "SpeechStarted", "UtteranceEnd"}:
        assert isinstance(value, str)
        return value
    return "Other"
