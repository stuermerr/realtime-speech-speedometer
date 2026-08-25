from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Mapping
from typing import Any, TypeVar

import pytest

from app.services.deepgram_transcription import (
    DeepgramProtocolError,
    DeepgramServiceError,
    ParsedDeepgramResult,
)
from app.services.live_wpm import LiveWpmPipeline, SessionWordState
from app.services.session_summary import FinalizedChunk
from app.services.wpm import RecognizedWord
from spikes.run_deepgram_transcription import run_probe
from spikes.run_realtime_transcription import NormalizedAudio


Result = TypeVar("Result")


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


def word(text: str, start: float, end: float) -> RecognizedWord:
    return RecognizedWord(text, start, end)


def result(
    *words: RecognizedWord, is_final: bool = False
) -> ParsedDeepgramResult:
    return ParsedDeepgramResult(
        is_final=is_final,
        text=" ".join(word.text for word in words),
        words=words,
    )


def test_only_non_empty_is_final_results_become_summary_chunks() -> None:
    pipeline = LiveWpmPipeline()
    interim = result(word("draft", 0.0, 0.5))
    empty_final = ParsedDeepgramResult(is_final=True, text="", words=())
    final = ParsedDeepgramResult(
        is_final=True,
        text="  Formatted final.  ",
        words=(word("formatted", 0.0, 0.5), word("final", 0.5, 1.0)),
    )

    pipeline.process_result(interim)
    pipeline.process_result(empty_final)
    pipeline.process_result(final)

    assert pipeline.finalized_chunks == (
        FinalizedChunk("  Formatted final.  ", final.words),
    )


def provider_result(
    words: tuple[RecognizedWord, ...], *, is_final: bool = False
) -> dict[str, object]:
    return {
        "type": "Results",
        "is_final": is_final,
        "channel": {
            "alternatives": [
                {
                    "transcript": " ".join(item.text for item in words),
                    "words": [
                        {
                            "word": item.text,
                            "start": item.start_seconds,
                            "end": item.end_seconds,
                        }
                        for item in words
                    ],
                }
            ]
        },
    }


def test_interim_results_replace_the_complete_visible_tail() -> None:
    state = SessionWordState()

    assert state.apply_result(result(word("hello", 0.0, 0.5))) is True
    assert state.words == (word("hello", 0.0, 0.5),)

    assert state.apply_result(
        result(word("hello", 0.0, 0.4), word("there", 0.5, 1.0))
    ) is True
    assert list(state.words) == [
        word("hello", 0.0, 0.4),
        word("there", 0.5, 1.0),
    ]

    assert state.apply_result(result()) is True
    assert list(state.words) == []


def test_final_results_discard_interim_and_accumulate_authoritative_chunks() -> None:
    state = SessionWordState()
    state.apply_result(result(word("draft", 0.0, 0.4)))

    assert state.apply_result(
        result(word("final", 0.0, 0.5), is_final=True)
    ) is True
    assert state.words == (word("final", 0.0, 0.5),)

    assert state.apply_result(
        result(word("history", 0.6, 1.1), is_final=True)
    ) is True
    assert list(state.words) == [
        word("final", 0.0, 0.5),
        word("history", 0.6, 1.1),
    ]


def test_identical_finalization_changes_ownership_without_visible_change() -> None:
    state = SessionWordState()
    words = (word("same", 0.0, 0.5),)
    state.apply_result(result(*words))

    assert state.apply_result(result(*words, is_final=True)) is False

    state.apply_result(result(word("draft", 0.6, 1.0)))
    state.apply_result(result())
    assert state.words == words


def test_candidate_is_validated_before_mutating_session_state() -> None:
    state = SessionWordState()
    finalized = word("stable", 1.0, 2.0)
    state.apply_result(result(finalized, is_final=True))

    with pytest.raises(DeepgramProtocolError, match="chronological"):
        state.apply_result(result(word("older", 0.5, 1.5)))

    assert state.words == (finalized,)


def test_overlapping_intervals_and_empty_finals_are_valid() -> None:
    state = SessionWordState()

    state.apply_result(result(word("one", 0.0, 1.0), is_final=True))
    state.apply_result(result(word("two", 0.8, 1.5), is_final=True))
    assert state.apply_result(result(is_final=True)) is False

    assert list(state.words) == [
        word("one", 0.0, 1.0),
        word("two", 0.8, 1.5),
    ]


def test_pipeline_distinguishes_unchanged_from_unavailable_measurement() -> None:
    pipeline = LiveWpmPipeline()
    short_interim = result(word("hello", 0.0, 0.5))

    measurement = pipeline.process_result(short_interim)

    assert measurement is not None
    assert measurement.wpm is None
    assert measurement.word_count == 1
    assert pipeline.process_result(short_interim) is None


def test_pipeline_recalculates_revised_interim_and_not_identical_finalization() -> None:
    pipeline = LiveWpmPipeline()
    initial = tuple(
        word(f"word-{index}", index * 0.5, (index + 1) * 0.5)
        for index in range(8)
    )
    revised = (*initial[:-1], word("revised", 3.5, 4.0))

    first = pipeline.process_result(result(*initial))
    second = pipeline.process_result(result(*revised))

    assert first is not None and first.wpm == 120.0
    assert second is not None and second.wpm == 120.0
    assert pipeline.process_result(result(*revised, is_final=True)) is None


def test_provider_events_flow_through_parsing_reconciliation_and_wpm() -> None:
    pipeline = LiveWpmPipeline()
    words = tuple(
        word(f"word-{index}", index * 0.5, (index + 1) * 0.5)
        for index in range(8)
    )

    assert pipeline.process_event({"type": "SpeechStarted"}) is None
    measurement = pipeline.process_event(provider_result(words))

    assert measurement is not None
    assert measurement.wpm == 120.0


def test_protocol_error_terminates_event_measurements() -> None:
    pipeline = LiveWpmPipeline()
    first = (word("first", 1.0, 1.5),)
    older = (word("older", 0.0, 0.5),)
    later = (word("later", 2.0, 2.5),)

    async def events() -> AsyncIterator[Mapping[str, object]]:
        yield provider_result(first, is_final=True)
        yield provider_result(older)
        yield provider_result(later)

    async def collect() -> list[object]:
        return [measurement async for measurement in pipeline.measure_events(events())]

    with pytest.raises(DeepgramProtocolError, match="chronological"):
        run(collect())

    measurement = pipeline.process_event(provider_result(later))
    assert measurement is not None
    assert measurement.word_count == 2


def test_provider_service_error_propagates_from_event_measurements() -> None:
    pipeline = LiveWpmPipeline()

    async def events() -> AsyncIterator[Mapping[str, object]]:
        yield {"type": "Error", "description": "provider detail"}

    async def collect() -> list[object]:
        return [measurement async for measurement in pipeline.measure_events(events())]

    with pytest.raises(DeepgramServiceError):
        run(collect())


def test_fixed_sample_probe_records_live_wpm_measurements() -> None:
    words = tuple(
        word(f"word-{index}", index * 0.5, (index + 1) * 0.5)
        for index in range(8)
    )

    class ProbeSession:
        async def send_audio(self, audio: bytes) -> None:
            pass

        async def close_stream(self) -> None:
            pass

        async def provider_events(self) -> AsyncIterator[Mapping[str, object]]:
            yield {"type": "Metadata", "request_id": "safe"}
            yield provider_result(words)

    probe = run(
        run_probe(
            ProbeSession(),  # type: ignore[arg-type]
            NormalizedAudio(pcm=b""),
            completion_timeout_seconds=1.0,
        )
    )

    assert len(probe.events) == 2
    assert len(probe.measurements) == 1
    assert probe.measurements[0].wpm == 120.0
