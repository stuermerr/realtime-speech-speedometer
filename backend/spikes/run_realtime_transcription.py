from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import wave
from array import array
from collections.abc import AsyncIterator, Awaitable, Callable
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Mapping, Protocol

from app.core.config import AzureSettings, ConfigurationError
from spikes.azure_transcription import (
    AzureTranscriptionSession,
    TranscriptionConnectionError,
    TranscriptionError,
)
from spikes.provider_event import ProviderEvent


PROVIDER_SAMPLE_RATE = 24_000
CHUNK_DURATION_SECONDS = 0.1
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 10.0
SAMPLE_PATH = Path(__file__).resolve().parents[2] / "samples" / "sample_01.wav"
ARTIFACT_DIRECTORY = Path(__file__).resolve().parent / ".artifacts"


class AudioSession(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...


class TranscriptionSession(AudioSession, Protocol):
    async def commit_audio(self) -> None: ...

    def events(self) -> AsyncIterator[ProviderEvent]: ...


@dataclass(frozen=True, slots=True)
class NormalizedAudio:
    pcm: bytes
    sample_rate: int = PROVIDER_SAMPLE_RATE
    channels: int = 1
    sample_width_bytes: int = 2

    @property
    def duration_seconds(self) -> float:
        bytes_per_second = (
            self.sample_rate * self.channels * self.sample_width_bytes
        )
        return len(self.pcm) / bytes_per_second


@dataclass(frozen=True, slots=True)
class ProbeResult:
    status: Literal["complete", "timeout", "error"]
    events: tuple[ProviderEvent, ...]
    audio_duration_seconds: float
    run_started_at_seconds: float
    run_finished_at_seconds: float


@dataclass(frozen=True, slots=True)
class CapturedRawEvent:
    received_at_seconds: float
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    raw_events: Path
    report: Path


class SpikeRunError(TranscriptionError):
    def __init__(self, message: str, artifacts: ArtifactPaths) -> None:
        super().__init__(message)
        self.artifacts = artifacts


def load_normalized_wav(path: Path) -> NormalizedAudio:
    """Load the fixed PCM16 mono sample and resample it to Azure's wire rate."""
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getcomptype() != "NONE":
            raise ValueError("The spike sample must be an uncompressed PCM WAV")
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError("The spike sample must be 16-bit mono PCM")
        source_rate = wav_file.getframerate()
        source_pcm = wav_file.readframes(wav_file.getnframes())

    if source_rate <= 0:
        raise ValueError("The spike sample must have a positive sample rate")
    if source_rate == PROVIDER_SAMPLE_RATE:
        return NormalizedAudio(pcm=source_pcm)

    samples = array("h")
    samples.frombytes(source_pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    output_count = round(len(samples) * PROVIDER_SAMPLE_RATE / source_rate)
    resampled = array("h")
    for output_index in range(output_count):
        source_position = output_index * source_rate / PROVIDER_SAMPLE_RATE
        left_index = min(int(source_position), len(samples) - 1)
        right_index = min(left_index + 1, len(samples) - 1)
        fraction = source_position - left_index
        value = round(
            samples[left_index]
            + (samples[right_index] - samples[left_index]) * fraction
        )
        resampled.append(value)

    if sys.byteorder != "little":
        resampled.byteswap()
    return NormalizedAudio(pcm=resampled.tobytes())


async def stream_audio(
    session: AudioSession,
    audio: NormalizedAudio,
    *,
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Send normalized PCM in 100 ms chunks paced on the audio timeline."""
    bytes_per_chunk = round(
        audio.sample_rate
        * audio.channels
        * audio.sample_width_bytes
        * CHUNK_DURATION_SECONDS
    )
    for offset in range(0, len(audio.pcm), bytes_per_chunk):
        if offset:
            await sleep(CHUNK_DURATION_SECONDS)
        await session.send_audio(audio.pcm[offset : offset + bytes_per_chunk])


async def run_transcription(
    session: TranscriptionSession,
    audio: NormalizedAudio,
    *,
    sleep: Callable[[float], Awaitable[None]],
    completion_timeout_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    """Stream one sample and wait a bounded time for a completed transcript."""
    run_started_at_seconds = clock()
    received_events: list[ProviderEvent] = []
    completion = asyncio.Event()
    input_finished = False
    completed_transcript_seen = False

    async def receive_events() -> None:
        nonlocal completed_transcript_seen
        async for event in session.events():
            received_events.append(event)
            if event.type in {
                "conversation.item.input_audio_transcription.completed",
                "response.text.done",
            }:
                completed_transcript_seen = True
                if input_finished:
                    completion.set()

    async def send_audio() -> None:
        await stream_audio(session, audio, sleep=sleep)
        await session.commit_audio()

    receiver = asyncio.create_task(receive_events())
    sender = asyncio.create_task(send_audio())
    completion_waiter: asyncio.Task[bool] | None = None
    try:
        first_done, _ = await asyncio.wait(
            (sender, receiver), return_when=asyncio.FIRST_COMPLETED
        )
        if sender not in first_done:
            await receiver
            raise TranscriptionConnectionError(
                "Azure transcription event stream ended before audio completed"
            )

        await sender
        input_finished = True
        if completed_transcript_seen:
            completion.set()

        completion_waiter = asyncio.create_task(completion.wait())
        final_done, _ = await asyncio.wait(
            (completion_waiter, receiver),
            timeout=completion_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if completion_waiter in final_done:
            status: Literal["complete", "timeout"] = "complete"
        elif receiver in final_done:
            await receiver
            raise TranscriptionConnectionError(
                "Azure transcription event stream ended before completion"
            )
        else:
            status = "timeout"
    finally:
        sender.cancel()
        receiver.cancel()
        if completion_waiter is None:
            await asyncio.gather(sender, receiver, return_exceptions=True)
        else:
            completion_waiter.cancel()
            await asyncio.gather(
                sender, receiver, completion_waiter, return_exceptions=True
            )

    return ProbeResult(
        status=status,
        events=tuple(received_events),
        audio_duration_seconds=audio.duration_seconds,
        run_started_at_seconds=run_started_at_seconds,
        run_finished_at_seconds=clock(),
    )


def write_artifacts(
    result: ProbeResult,
    raw_events: list[CapturedRawEvent],
    *,
    artifact_directory: Path,
    run_id: str,
) -> ArtifactPaths:
    """Write local-only raw evidence and a timing-oriented run report."""
    artifact_directory.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_directory / f"{run_id}-events.jsonl"
    report_path = artifact_directory / f"{run_id}-report.json"

    raw_lines = [
        json.dumps(
            {
                "received_offset_seconds": (
                    event.received_at_seconds - result.run_started_at_seconds
                ),
                "event": dict(event.payload),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for event in raw_events
    ]
    raw_path.write_text(
        "\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8"
    )

    report = {
        "status": result.status,
        "audio_duration_seconds": result.audio_duration_seconds,
        "run_duration_seconds": (
            result.run_finished_at_seconds - result.run_started_at_seconds
        ),
        "event_count": len(result.events),
        "event_types": dict(
            sorted(Counter(event.type for event in result.events).items())
        ),
        "events": [
            {
                "received_offset_seconds": (
                    event.received_at_seconds - result.run_started_at_seconds
                ),
                "type": event.type,
                "fields": dict(event.fields),
            }
            for event in result.events
        ],
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ArtifactPaths(raw_events=raw_path, report=report_path)


def completion_timeout_from_environment() -> float:
    raw_value = os.environ.get(
        "TRANSCRIPTION_SPIKE_COMPLETION_TIMEOUT_SECONDS",
        str(DEFAULT_COMPLETION_TIMEOUT_SECONDS),
    )
    try:
        timeout = float(raw_value)
    except ValueError as error:
        raise ValueError(
            "TRANSCRIPTION_SPIKE_COMPLETION_TIMEOUT_SECONDS must be a number"
        ) from error
    if timeout <= 0:
        raise ValueError(
            "TRANSCRIPTION_SPIKE_COMPLETION_TIMEOUT_SECONDS must be positive"
        )
    return timeout


async def run_fixed_sample() -> tuple[ProbeResult, ArtifactPaths]:
    """Connect to Azure and capture one fixed-sample evidence run."""
    settings = AzureSettings.from_environment()
    audio = load_normalized_wav(SAMPLE_PATH)
    raw_events: list[CapturedRawEvent] = []
    run_started_at_seconds = time.monotonic()

    def capture_raw_event(
        received_at_seconds: float, payload: Mapping[str, object]
    ) -> None:
        raw_events.append(CapturedRawEvent(received_at_seconds, dict(payload)))

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        async with AzureTranscriptionSession(
            settings, raw_event_observer=capture_raw_event
        ) as session:
            result = await run_transcription(
                session,
                audio,
                sleep=asyncio.sleep,
                completion_timeout_seconds=completion_timeout_from_environment(),
            )
    except TranscriptionError as error:
        failed_result = ProbeResult(
            status="error",
            events=(),
            audio_duration_seconds=audio.duration_seconds,
            run_started_at_seconds=run_started_at_seconds,
            run_finished_at_seconds=time.monotonic(),
        )
        paths = write_artifacts(
            failed_result,
            raw_events,
            artifact_directory=ARTIFACT_DIRECTORY,
            run_id=run_id,
        )
        raise SpikeRunError(str(error), paths) from error

    paths = write_artifacts(
        result,
        raw_events,
        artifact_directory=ARTIFACT_DIRECTORY,
        run_id=run_id,
    )
    return result, paths


def main() -> int:
    try:
        result, paths = asyncio.run(run_fixed_sample())
    except SpikeRunError as error:
        print(
            f"Spike failed: {error}; report={error.artifacts.report}; "
            f"raw={error.artifacts.raw_events}",
            file=sys.stderr,
        )
        return 1
    except (ConfigurationError, TranscriptionError, ValueError, OSError) as error:
        print(f"Spike failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Spike {result.status}: {len(result.events)} provider events; "
        f"report={paths.report}; raw={paths.raw_events}"
    )
    return 0 if result.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
