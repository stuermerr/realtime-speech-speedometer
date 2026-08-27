"""One browser microphone session coordinated with one Deepgram stream."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.services.deepgram_transcription import (
    DeepgramError,
    ParsedDeepgramResult,
    parse_deepgram_event,
)
from app.services.live_wpm import LiveWpmPipeline
from app.services.session_summary import (
    SessionSummary,
    SessionSummaryCalculator,
    SummarySegment,
)
from app.services.wpm import (
    ActiveSpeechPolicy,
    ActiveSpeechWpm,
    DualWindowActiveSpeechWpm,
    PaceStatus,
    WpmMeasurement,
    classify_pace,
)


_diagnostic_logger = logging.getLogger("uvicorn.error.speech_speedometer.live_wpm")
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
        live_window_seconds: float | None = None,
        live_minimum_active_seconds: float | None = None,
    ) -> None:
        self._enabled = enabled
        if enabled and sink is None:
            _diagnostic_logger.setLevel(logging.INFO)
        self._sink = _diagnostic_logger.info if sink is None else sink
        self._clock = clock
        self._started_at = clock()
        self._session_id = uuid.uuid4().hex if session_id is None else session_id
        self._live_window_seconds = live_window_seconds
        self._live_minimum_active_seconds = live_minimum_active_seconds

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

    def startup_fields(self) -> dict[str, DiagnosticValue]:
        fields: dict[str, DiagnosticValue] = {}
        if self._live_window_seconds is not None:
            fields["live_window_seconds"] = self._live_window_seconds
        if self._live_minimum_active_seconds is not None:
            fields["live_minimum_active_seconds"] = self._live_minimum_active_seconds
        return fields


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
    pace_status: PaceStatus | None
    word_count: int
    active_speech_seconds: float
    audio_start_seconds: float | None
    audio_end_seconds: float | None

    @model_validator(mode="after")
    def validate_pace_availability(self) -> Self:
        if (self.wpm is None) != (self.pace_status is None):
            raise ValueError("WPM and pace status availability must match")
        return self

    @classmethod
    def from_measurement(cls, measurement: WpmMeasurement) -> _MeasurementMessage:
        return cls(
            wpm=measurement.wpm,
            pace_status=classify_pace(measurement.wpm),
            word_count=measurement.word_count,
            active_speech_seconds=measurement.active_speech_seconds,
            audio_start_seconds=measurement.audio_start_seconds,
            audio_end_seconds=measurement.audio_end_seconds,
        )


class _StoppedMessage(BaseModel):
    type: Literal["stopped"] = "stopped"
    reason: Literal["user", "inactivity"]


class _SummaryMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["summary"] = "summary"
    average_speaking_pace: float | None
    finalized_words: int
    active_speaking_seconds: float
    presentation_duration_seconds: float
    segments: tuple[SummarySegment, ...]

    @classmethod
    def from_summary(cls, summary: SessionSummary) -> Self:
        return cls(
            average_speaking_pace=summary.average_speaking_pace,
            finalized_words=summary.finalized_words,
            active_speaking_seconds=summary.active_speaking_seconds,
            presentation_duration_seconds=summary.presentation_duration_seconds,
            segments=summary.segments,
        )


class _StopRequestedMessage(BaseModel):
    type: Literal["stop_requested"] = "stop_requested"
    reason: Literal["inactivity"] = "inactivity"


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
        live_wpm_calculator: ActiveSpeechWpm | DualWindowActiveSpeechWpm | None = None,
        summary_policy: ActiveSpeechPolicy | None = None,
        diagnostics: LiveWpmDiagnostics | None = None,
        drain_timeout_seconds: float = 5.0,
        inactivity_timeout_seconds: float = 300.0,
        stop_ack_timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if drain_timeout_seconds <= 0:
            raise ValueError("Provider drain timeout must be positive")
        if inactivity_timeout_seconds <= 0 or stop_ack_timeout_seconds <= 0:
            raise ValueError("Session timeouts must be positive")
        self._browser = browser
        self._provider = provider
        self._drain_timeout_seconds = drain_timeout_seconds
        self._inactivity_timeout_seconds = inactivity_timeout_seconds
        self._stop_ack_timeout_seconds = stop_ack_timeout_seconds
        self._clock = clock
        self._pipeline = LiveWpmPipeline(calculator=live_wpm_calculator)
        self._summary_calculator = SessionSummaryCalculator(policy=summary_policy)
        self._last_recognized_progress_at = clock()
        self._maximum_recognized_end: float | None = None
        self._diagnostics = (
            LiveWpmDiagnostics(enabled=False) if diagnostics is None else diagnostics
        )

    async def run(self) -> None:
        browser_task: asyncio.Task[None] | None = None
        provider_task: asyncio.Task[bool] | None = None
        inactivity_task: asyncio.Task[None] | None = None
        self._diagnostics.record(
            "session", "started", **self._diagnostics.startup_fields()
        )
        try:
            self._diagnostics.record("provider", "opening")
            async with self._provider:
                self._diagnostics.record("provider", "opened")

                # These flows run concurrently so browser audio can move upstream
                # while provider transcripts and inactivity checks move downstream.
                browser_task = asyncio.create_task(self._forward_browser())
                provider_task = asyncio.create_task(self._forward_provider())
                inactivity_task = asyncio.create_task(self._wait_for_inactivity())
                done, _ = await asyncio.wait(
                    (browser_task, provider_task, inactivity_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if browser_task in done:
                    try:
                        await browser_task
                    except BaseException:
                        await _cancel(provider_task)
                        raise
                    # Stop closes Deepgram's input but not its output. Drain the
                    # remaining final Results before calculating the summary.
                    metadata_seen = await self._drain_provider(provider_task)
                    if not metadata_seen:
                        raise BrowserSessionProtocolError(
                            "Deepgram closed without final Metadata"
                        )
                    await self._send_completion("user")
                    return

                if inactivity_task in done:
                    await inactivity_task
                    self._diagnostics.record("session", "inactivity_requested")
                    await self._send(_StopRequestedMessage())
                    try:
                        async with asyncio.timeout(self._stop_ack_timeout_seconds):
                            await browser_task
                    except TimeoutError:
                        raise BrowserSessionProtocolError(
                            "Browser did not acknowledge inactivity stop"
                        ) from None
                    metadata_seen = await self._drain_provider(provider_task)
                    if not metadata_seen:
                        raise BrowserSessionProtocolError(
                            "Deepgram closed without final Metadata"
                        )
                    await self._send_completion("inactivity")
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
            await _cancel(inactivity_task)
            self._diagnostics.record("session", "cleanup_finished")

    async def _forward_browser(self) -> None:
        try:
            await self._forward_browser_messages()
        except asyncio.CancelledError:
            self._diagnostics.record("browser", "flow_cancelled")
            raise

    async def _forward_browser_messages(self) -> None:
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
                # Deliberately no internal queue: WebSocket backpressure bounds
                # memory if Deepgram is temporarily slower than the browser.
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
                try:
                    result = parse_deepgram_event(event)
                except DeepgramError:
                    self._diagnostics.record(
                        "provider",
                        "event_received",
                        provider_event_type=event_type,
                    )
                    raise
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
                self._record_recognized_progress(result)
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

                # WPM is emitted only for a changed word timeline. During pauses no
                # event is sent, so the browser naturally retains its last value.
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

    async def _wait_for_inactivity(self) -> None:
        while True:
            remaining = self._inactivity_timeout_seconds - (
                self._clock() - self._last_recognized_progress_at
            )
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)

    def _record_recognized_progress(self, result: ParsedDeepgramResult) -> None:
        end = max((word.end_seconds for word in result.words), default=None)
        if end is not None and (
            self._maximum_recognized_end is None or end > self._maximum_recognized_end
        ):
            self._maximum_recognized_end = end
            self._last_recognized_progress_at = self._clock()

    async def _send_completion(self, reason: Literal["user", "inactivity"]) -> None:
        # Only provider-final chunks enter the recap; interim hypotheses may still
        # be corrected and therefore must never become summary evidence.
        summary = self._summary_calculator.build(self._pipeline.finalized_chunks)
        await self._send(_SummaryMessage.from_summary(summary))
        await self._send(_StoppedMessage(reason=reason))
        self._diagnostics.record("browser", "stopped_sent", reason=reason)

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
    if value in {
        "Results",
        "Metadata",
        "SpeechStarted",
        "UtteranceEnd",
        "Error",
    }:
        assert isinstance(value, str)
        return value
    return "Other"
