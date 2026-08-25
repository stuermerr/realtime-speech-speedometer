from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast

from app.core.config import ConfigurationError, DeepgramSettings
from app.services.deepgram_transcription import (
    DEEPGRAM_CHANNELS,
    DEEPGRAM_ENCODING,
    DEEPGRAM_ENDPOINTING_MILLISECONDS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_QUERY_PARAMETERS,
    DEEPGRAM_SAMPLE_RATE,
    DEEPGRAM_UTTERANCE_END_MILLISECONDS,
    DeepgramAudioMode,
    DeepgramError,
    DeepgramTranscriptionSession,
)
from app.services.live_wpm import LiveWpmPipeline
from app.services.wpm import WpmMeasurement
from spikes.run_realtime_transcription import (
    ARTIFACT_DIRECTORY,
    NormalizedAudio,
    load_normalized_wav,
    stream_audio,
)
from spikes.provider_event import ProviderEvent


SAMPLE_PATH = Path(__file__).resolve().parents[2] / "samples" / "sample_02.wav"
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class DeepgramProbeResult:
    status: str
    events: tuple[ProviderEvent, ...]
    measurements: tuple[WpmMeasurement, ...]
    audio_duration_seconds: float
    run_started_at_seconds: float
    run_finished_at_seconds: float


async def run_probe(
    session: DeepgramTranscriptionSession,
    audio: NormalizedAudio,
    *,
    completion_timeout_seconds: float,
) -> DeepgramProbeResult:
    started_at = time.monotonic()
    events: list[ProviderEvent] = []
    measurements: list[WpmMeasurement] = []
    pipeline = LiveWpmPipeline()

    async def receive() -> None:
        async for payload in session.provider_events():
            event_type = cast(str, payload["type"])
            events.append(
                ProviderEvent(
                    received_at_seconds=time.monotonic(),
                    type=event_type,
                    fields=MappingProxyType(dict(payload)),
                )
            )
            measurement = pipeline.process_event(payload)
            if measurement is not None:
                measurements.append(measurement)

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
        measurements=tuple(measurements),
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
    channel_label = (
        "mono" if DEEPGRAM_CHANNELS == 1 else f"{DEEPGRAM_CHANNELS} channels"
    )
    report = {
        "status": result.status,
        "configuration": {
            "model": DEEPGRAM_MODEL,
            "language": DEEPGRAM_LANGUAGE,
            "endpointing_ms": DEEPGRAM_ENDPOINTING_MILLISECONDS,
            "utterance_end_ms": DEEPGRAM_UTTERANCE_END_MILLISECONDS,
            "vad_events": DEEPGRAM_QUERY_PARAMETERS["vad_events"] == "true",
            "interim_results": (
                DEEPGRAM_QUERY_PARAMETERS["interim_results"] == "true"
            ),
            "audio": f"{DEEPGRAM_ENCODING} {channel_label} {DEEPGRAM_SAMPLE_RATE} Hz",
        },
        "audio_duration_seconds": result.audio_duration_seconds,
        "run_duration_seconds": (
            result.run_finished_at_seconds - result.run_started_at_seconds
        ),
        "event_types": dict(
            sorted(Counter(event.type for event in result.events).items())
        ),
        "wpm_measurements": [
            {
                "wpm": measurement.wpm,
                "word_count": measurement.word_count,
                "active_speech_seconds": measurement.active_speech_seconds,
                "audio_start_seconds": measurement.audio_start_seconds,
                "audio_end_seconds": measurement.audio_end_seconds,
            }
            for measurement in result.measurements
        ],
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
    async with DeepgramTranscriptionSession(
        settings,
        audio_mode=DeepgramAudioMode.LINEAR16_24KHZ_MONO,
    ) as session:
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
        f"{len(result.measurements)} WPM measurements; report={report_path}"
    )
    return 0 if result.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
