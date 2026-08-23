from __future__ import annotations

import asyncio
import json
import struct
import wave
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

from spikes.azure_transcription import TranscriptionConnectionError
from spikes.provider_event import ProviderEvent
from spikes.run_realtime_transcription import (
    CapturedRawEvent,
    NormalizedAudio,
    ProbeResult,
    load_normalized_wav,
    run_transcription,
    stream_audio,
    write_artifacts,
)


Result = TypeVar("Result")


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


def write_pcm16_wav(
    path: Path, *, sample_rate: int, samples: list[int], channels: int = 1
) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def test_fixed_sample_is_normalized_to_provider_wire_format(tmp_path: Path) -> None:
    source = tmp_path / "sample.wav"
    write_pcm16_wav(
        source,
        sample_rate=48_000,
        samples=[1000, -1000] * 2_400,
    )

    audio = load_normalized_wav(source)

    assert audio.sample_rate == 24_000
    assert audio.channels == 1
    assert audio.sample_width_bytes == 2
    assert audio.duration_seconds == pytest.approx(0.1)
    assert len(audio.pcm) == 4_800


def test_normalized_audio_is_streamed_in_paced_100_ms_chunks() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.chunks: list[bytes] = []

        async def send_audio(self, audio: bytes) -> None:
            self.chunks.append(audio)

    async def scenario() -> tuple[list[bytes], list[float]]:
        session = RecordingSession()
        sleeps: list[float] = []

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        audio = NormalizedAudio(pcm=b"x" * 12_000)
        await stream_audio(session, audio, sleep=record_sleep)
        return session.chunks, sleeps

    chunks, sleeps = run(scenario())

    assert [len(chunk) for chunk in chunks] == [4_800, 4_800, 2_400]
    assert sleeps == [0.1, 0.1]


def test_transcription_run_reports_timeout_instead_of_hanging() -> None:
    class NeverCompletingSession:
        async def send_audio(self, audio: bytes) -> None:
            pass

        async def commit_audio(self) -> None:
            pass

        async def events(self) -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(0.1, "session.updated", {})
            await asyncio.Event().wait()

    async def no_sleep(seconds: float) -> None:
        pass

    result = run(
        run_transcription(
            NeverCompletingSession(),
            NormalizedAudio(pcm=b"x" * 4_800),
            sleep=no_sleep,
            completion_timeout_seconds=0.01,
        )
    )

    assert result.status == "timeout"
    assert result.events[0].type == "session.updated"


def test_transcription_run_completes_when_final_transcript_arrives() -> None:
    class CompletingSession:
        def __init__(self) -> None:
            self.commit_count = 0

        async def send_audio(self, audio: bytes) -> None:
            pass

        async def commit_audio(self) -> None:
            self.commit_count += 1

        async def events(self) -> AsyncIterator[ProviderEvent]:
            await asyncio.sleep(0)
            yield ProviderEvent(
                1.25,
                "conversation.item.input_audio_transcription.completed",
                {"transcript": "Good morning"},
            )
            await asyncio.Event().wait()

    async def no_sleep(seconds: float) -> None:
        pass

    session = CompletingSession()
    result = run(
        run_transcription(
            session,
            NormalizedAudio(pcm=b"x" * 4_800),
            sleep=no_sleep,
            completion_timeout_seconds=1,
        )
    )

    assert result.status == "complete"
    assert session.commit_count == 1
    assert result.events[-1].fields == {"transcript": "Good morning"}


def test_transcription_run_reports_an_event_stream_that_ends_early() -> None:
    class EndedSession:
        async def send_audio(self, audio: bytes) -> None:
            pass

        async def commit_audio(self) -> None:
            pass

        async def events(self) -> AsyncIterator[ProviderEvent]:
            if False:
                yield ProviderEvent(0, "unreachable", {})

    async def no_sleep(seconds: float) -> None:
        pass

    with pytest.raises(TranscriptionConnectionError, match="ended before"):
        run(
            run_transcription(
                EndedSession(),
                NormalizedAudio(pcm=b"x" * 4_800),
                sleep=no_sleep,
                completion_timeout_seconds=1,
            )
        )


def test_local_artifacts_preserve_raw_events_and_report_timing(tmp_path: Path) -> None:
    result = ProbeResult(
        status="complete",
        events=(
            ProviderEvent(
                10.5,
                "conversation.item.input_audio_transcription.segment",
                {
                    "id": "seg-1",
                    "text": "Good morning",
                    "speaker": "spk-1",
                    "start": 0.1,
                    "end": 0.9,
                },
            ),
        ),
        audio_duration_seconds=2.0,
        run_started_at_seconds=10.0,
        run_finished_at_seconds=12.25,
    )
    raw_events = [
        CapturedRawEvent(
            10.25,
            {
                "type": "session.created",
                "provider_debug_field": {"sequence": 1},
            },
        )
    ]

    paths = write_artifacts(
        result,
        raw_events,
        artifact_directory=tmp_path,
        run_id="test-run",
    )

    raw_line = json.loads(paths.raw_events.read_text().strip())
    report = json.loads(paths.report.read_text())
    assert raw_line["event"]["provider_debug_field"] == {"sequence": 1}
    assert raw_line["received_offset_seconds"] == pytest.approx(0.25)
    assert report["status"] == "complete"
    assert report["run_duration_seconds"] == pytest.approx(2.25)
    assert report["event_types"] == {
        "conversation.item.input_audio_transcription.segment": 1
    }
    assert report["events"][0]["received_offset_seconds"] == pytest.approx(0.5)
    assert report["events"][0]["fields"] == {
        "id": "seg-1",
        "text": "Good morning",
        "speaker": "spk-1",
        "start": 0.1,
        "end": 0.9,
    }
