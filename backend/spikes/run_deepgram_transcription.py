from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, Self, cast
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.core.config import ConfigurationError, load_backend_environment
from spikes.run_realtime_transcription import (
    ARTIFACT_DIRECTORY,
    NormalizedAudio,
    load_normalized_wav,
    stream_audio,
)
from spikes.provider_event import ProviderEvent


SAMPLE_PATH = Path(__file__).resolve().parents[2] / "samples" / "sample_02.wav"
DEEPGRAM_WEBSOCKET_URL = "wss://api.deepgram.com/v1/listen"
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 10.0


class DeepgramError(RuntimeError):
    """Base class for secret-safe Deepgram spike failures."""


class _DeepgramConnection(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def send(self, message: str | bytes) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DeepgramSettings:
    api_key: str = field(repr=False)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DeepgramSettings:
        if environment is None:
            environment = load_backend_environment()

        api_key = environment.get("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "Missing required Deepgram configuration: DEEPGRAM_API_KEY"
            )
        return cls(api_key=api_key)


@dataclass(frozen=True, slots=True)
class DeepgramProbeResult:
    status: str
    events: tuple[ProviderEvent, ...]
    audio_duration_seconds: float
    run_started_at_seconds: float
    run_finished_at_seconds: float


class DeepgramTranscriptionSession:
    """One direct Deepgram WebSocket used only by the comparison spike."""

    def __init__(self, settings: DeepgramSettings) -> None:
        self._settings = settings
        self._connection: _DeepgramConnection | None = None

    async def __aenter__(self) -> Self:
        parameters = urlencode(
            {
                "model": "nova-3",
                "language": "de",
                "encoding": "linear16",
                "sample_rate": "24000",
                "channels": "1",
                "interim_results": "true",
                "punctuate": "true",
                "smart_format": "true",
                "vad_events": "true",
                "endpointing": "600",
                "utterance_end_ms": "1000",
            }
        )
        try:
            self._connection = cast(
                _DeepgramConnection,
                await connect(
                    f"{DEEPGRAM_WEBSOCKET_URL}?{parameters}",
                    additional_headers={
                        "Authorization": f"Token {self._settings.api_key}"
                    },
                ),
            )
        except Exception as error:
            raise DeepgramError(_safe_connection_error(error)) from error
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            await connection.close()

    async def send_audio(self, audio: bytes) -> None:
        connection = self._require_connection()
        await connection.send(audio)

    async def close_stream(self) -> None:
        connection = self._require_connection()
        await connection.send(json.dumps({"type": "CloseStream"}))

    async def events(self) -> AsyncIterator[ProviderEvent]:
        connection = self._require_connection()
        try:
            async for message in connection:
                received_at = time.monotonic()
                try:
                    payload = json.loads(message)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise DeepgramError("Deepgram sent malformed JSON") from error
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("type"), str
                ):
                    raise DeepgramError(
                        "Deepgram sent an event without a valid type"
                    )
                typed_payload = cast(dict[str, object], payload)
                if typed_payload["type"] == "Error":
                    raise DeepgramError("Deepgram reported a service error")
                yield ProviderEvent(
                    received_at_seconds=received_at,
                    type=cast(str, typed_payload["type"]),
                    fields=MappingProxyType(typed_payload.copy()),
                )
        except ConnectionClosed:
            return

    def _require_connection(self) -> _DeepgramConnection:
        if self._connection is None:
            raise DeepgramError("Deepgram transcription session is not open")
        return self._connection


def _safe_connection_error(error: Exception) -> str:
    if isinstance(error, InvalidStatus):
        if error.response.status_code in (401, 403):
            return "Deepgram authentication was rejected"
        return "Deepgram rejected the WebSocket connection"
    return "Could not connect to Deepgram"


async def run_probe(
    session: DeepgramTranscriptionSession,
    audio: NormalizedAudio,
    *,
    completion_timeout_seconds: float,
) -> DeepgramProbeResult:
    started_at = time.monotonic()
    events: list[ProviderEvent] = []

    async def receive() -> None:
        async for event in session.events():
            events.append(event)

    receiver = asyncio.create_task(receive())
    try:
        await stream_audio(session, audio, sleep=asyncio.sleep)
        await session.close_stream()
        try:
            await asyncio.wait_for(receiver, timeout=completion_timeout_seconds)
            status = "complete"
        except TimeoutError:
            status = "timeout"
    finally:
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)

    return DeepgramProbeResult(
        status=status,
        events=tuple(events),
        audio_duration_seconds=audio.duration_seconds,
        run_started_at_seconds=started_at,
        run_finished_at_seconds=time.monotonic(),
    )


def _event_summary(event: ProviderEvent) -> dict[str, object]:
    payload = event.fields
    summary: dict[str, object] = {"type": event.type}
    for name in ("start", "duration", "is_final", "speech_final", "timestamp"):
        if name in payload:
            summary[name] = payload[name]
    if event.type == "UtteranceEnd" and "last_word_end" in payload:
        summary["last_word_end"] = payload["last_word_end"]
    channel = payload.get("channel")
    if isinstance(channel, dict):
        alternatives = channel.get("alternatives")
        if isinstance(alternatives, list) and alternatives:
            alternative = alternatives[0]
            if isinstance(alternative, dict):
                summary["transcript"] = alternative.get("transcript", "")
                words = alternative.get("words")
                if isinstance(words, list):
                    summary["words"] = words
    return summary


def write_report(result: DeepgramProbeResult, *, run_id: str) -> Path:
    ARTIFACT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    report_path = ARTIFACT_DIRECTORY / f"{run_id}-deepgram-report.json"
    summaries = [_event_summary(event) for event in result.events]
    final_words = [
        word
        for event in summaries
        if event.get("type") == "Results" and event.get("is_final") is True
        for word in cast(list[object], event.get("words", []))
        if isinstance(word, dict)
    ]
    word_gaps = []
    for previous, current in zip(final_words, final_words[1:]):
        previous_end = previous.get("end")
        current_start = current.get("start")
        if isinstance(previous_end, (int, float)) and isinstance(
            current_start, (int, float)
        ):
            word_gaps.append(
                {
                    "seconds": current_start - previous_end,
                    "after": previous.get("word"),
                    "before": current.get("word"),
                }
            )
    report = {
        "status": result.status,
        "configuration": {
            "model": "nova-3",
            "language": "de",
            "endpointing_ms": 600,
            "utterance_end_ms": 1000,
            "vad_events": True,
            "interim_results": True,
            "audio": "linear16 mono 24000 Hz",
        },
        "audio_duration_seconds": result.audio_duration_seconds,
        "run_duration_seconds": (
            result.run_finished_at_seconds - result.run_started_at_seconds
        ),
        "event_types": dict(
            sorted(Counter(event.type for event in result.events).items())
        ),
        "results": summaries,
        "largest_final_word_gaps": sorted(
            word_gaps, key=lambda gap: cast(float, gap["seconds"]), reverse=True
        )[:5],
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


async def run_fixed_sample() -> tuple[DeepgramProbeResult, Path]:
    settings = DeepgramSettings.from_environment()
    audio = load_normalized_wav(SAMPLE_PATH)
    timeout = float(
        os.environ.get(
            "TRANSCRIPTION_SPIKE_COMPLETION_TIMEOUT_SECONDS",
            DEFAULT_COMPLETION_TIMEOUT_SECONDS,
        )
    )
    if timeout <= 0:
        raise ValueError("Completion timeout must be positive")
    async with DeepgramTranscriptionSession(settings) as session:
        result = await run_probe(
            session, audio, completion_timeout_seconds=timeout
        )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return result, write_report(result, run_id=run_id)


def main() -> int:
    try:
        result, report_path = asyncio.run(run_fixed_sample())
    except (ConfigurationError, DeepgramError, ValueError, OSError) as error:
        print(f"Deepgram spike failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Deepgram spike {result.status}: {len(result.events)} events; "
        f"report={report_path}"
    )
    return 0 if result.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
