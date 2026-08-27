"""Session-owned reconciliation for live Deepgram pace measurements."""

from __future__ import annotations

import math
from collections.abc import AsyncIterable, AsyncIterator, Mapping

from app.services.deepgram_transcription import (
    DeepgramProtocolError,
    ParsedDeepgramResult,
    parse_deepgram_event,
)
from app.services.wpm import (
    ActiveSpeechPolicy,
    ActiveSpeechWpm,
    DualWindowActiveSpeechWpm,
    RecognizedWord,
    WpmMeasurement,
)
from app.services.session_summary import FinalizedChunk


class SessionWordState:
    """Own finalized words and the current replaceable interim hypothesis."""

    def __init__(self) -> None:
        self._finalized_words: tuple[RecognizedWord, ...] = ()
        self._interim_words: tuple[RecognizedWord, ...] = ()
        self._finalized_chunks: tuple[FinalizedChunk, ...] = ()

    @property
    def words(self) -> tuple[RecognizedWord, ...]:
        """Return the complete current chronological word timeline."""
        return self._finalized_words + self._interim_words

    @property
    def finalized_words(self) -> tuple[RecognizedWord, ...]:
        """Return the immutable provider-finalized timeline for session summary."""
        return self._finalized_words

    @property
    def finalized_chunks(self) -> tuple[FinalizedChunk, ...]:
        return self._finalized_chunks

    def apply_result(self, result: ParsedDeepgramResult) -> bool:
        """Apply one atomic hypothesis and report a visible timeline change."""
        previous_words = self.words

        # Deepgram interims are full replacement hypotheses, not deltas. Finals
        # move into immutable history and clear that replaceable interim tail.
        if result.is_final:
            candidate_finalized = self._finalized_words + result.words
            candidate_interim: tuple[RecognizedWord, ...] = ()
        else:
            candidate_finalized = self._finalized_words
            candidate_interim = result.words

        candidate_words = candidate_finalized + candidate_interim
        _validate_candidate(candidate_words)
        self._finalized_words = candidate_finalized
        self._interim_words = candidate_interim
        if result.is_final and result.words:
            self._finalized_chunks += (FinalizedChunk(result.text, result.words),)
        return self.words != previous_words


class LiveWpmPipeline:
    """Reconcile one session's Results and calculate changed timelines."""

    def __init__(
        self,
        *,
        calculator: ActiveSpeechWpm | DualWindowActiveSpeechWpm | None = None,
        policy: ActiveSpeechPolicy | None = None,
    ) -> None:
        if calculator is not None and policy is not None:
            raise ValueError("Use either a WPM calculator or active-speech policy")
        self._word_state = SessionWordState()
        self._calculator = (
            ActiveSpeechWpm(policy=policy) if calculator is None else calculator
        )

    def process_result(self, result: ParsedDeepgramResult) -> WpmMeasurement | None:
        """Return a measurement only when the visible timeline changes."""
        if not self._word_state.apply_result(result):
            return None

        # Recalculate from the complete corrected timeline, making the same input
        # deterministic even when Deepgram revises an interim transcript.
        return self._calculator.calculate(self._word_state.words)

    @property
    def finalized_words(self) -> tuple[RecognizedWord, ...]:
        """Expose only finalized evidence after provider drain."""
        return self._word_state.finalized_words

    @property
    def finalized_chunks(self) -> tuple[FinalizedChunk, ...]:
        return self._word_state.finalized_chunks

    def process_event(self, payload: Mapping[str, object]) -> WpmMeasurement | None:
        """Parse and process one provider event; ignore known non-Results."""
        result = parse_deepgram_event(payload)
        if result is None:
            return None
        return self.process_result(result)

    async def measure_events(
        self, events: AsyncIterable[Mapping[str, object]]
    ) -> AsyncIterator[WpmMeasurement]:
        """Yield changed-timeline measurements until the event stream ends."""
        async for payload in events:
            measurement = self.process_event(payload)
            if measurement is not None:
                yield measurement


def _validate_candidate(words: tuple[RecognizedWord, ...]) -> None:
    previous_start: float | None = None
    for word in words:
        if (
            not word.text.strip()
            or not math.isfinite(word.start_seconds)
            or not math.isfinite(word.end_seconds)
            or word.start_seconds < 0
            or word.end_seconds <= word.start_seconds
        ):
            raise DeepgramProtocolError("Deepgram Result contains an invalid word")
        if previous_start is not None and word.start_seconds < previous_start:
            raise DeepgramProtocolError(
                "Deepgram Result word timeline is not chronological"
            )
        previous_start = word.start_seconds
