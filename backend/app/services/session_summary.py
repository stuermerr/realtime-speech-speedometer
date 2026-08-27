"""Deterministic global metrics for one finalized presentation timeline."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from app.services.wpm import (
    ActiveSpeechPolicy,
    PaceStatus,
    RecognizedWord,
    classify_pace,
)


@dataclass(frozen=True, slots=True)
class FinalizedChunk:
    """One non-empty provider-final chunk retained in provider order."""

    text: str
    words: tuple[RecognizedWord, ...]


@dataclass(frozen=True, slots=True)
class SummarySegment:
    """Decision-rich pace recap for one group of whole provider chunks."""

    text: str
    average_speaking_pace: float | None
    pace_status: PaceStatus | None


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Unrounded quantitative metrics calculated from finalized words only."""

    average_speaking_pace: float | None
    finalized_words: int
    active_speaking_seconds: float
    presentation_duration_seconds: float
    segments: tuple[SummarySegment, ...]


class SessionSummaryCalculator:
    """Build one global summary with the established active-speech policy."""

    def __init__(
        self,
        *,
        policy: ActiveSpeechPolicy | None = None,
    ) -> None:
        self._policy = ActiveSpeechPolicy() if policy is None else policy

    def build(self, finalized_chunks: Iterable[FinalizedChunk]) -> SessionSummary:
        """Calculate global metrics and segments from immutable final chunks."""
        # Materializing once gives every summary metric the same finalized snapshot.
        chunks = tuple(finalized_chunks)
        words = _flatten_words(chunks)
        if not words:
            return SessionSummary(None, 0, 0.0, 0.0, ())

        active_seconds, average_pace = self._calculate_pace_metrics(words)
        return SessionSummary(
            average_speaking_pace=average_pace,
            finalized_words=len(words),
            active_speaking_seconds=active_seconds,
            presentation_duration_seconds=(
                max(word.end_seconds for word in words) - words[0].start_seconds
            ),
            segments=self._build_segments(chunks),
        )

    def _build_segments(
        self, chunks: tuple[FinalizedChunk, ...]
    ) -> tuple[SummarySegment, ...]:
        groups: list[tuple[FinalizedChunk, ...]] = []
        pending: list[FinalizedChunk] = []
        for chunk in chunks:
            pending.append(chunk)
            # Keep provider-final chunks whole while accumulating enough active
            # speech for each segment's WPM to be meaningful.
            if (
                self._active_seconds(pending)
                >= self._policy.minimum_active_seconds
            ):
                groups.append(tuple(pending))
                pending = []
        if pending:
            if groups:
                groups[-1] = groups[-1] + tuple(pending)
            else:
                groups.append(tuple(pending))
        return tuple(self._segment(group) for group in groups)

    def _active_seconds(self, chunks: Iterable[FinalizedChunk]) -> float:
        return self._calculate_pace_metrics(_flatten_words(chunks))[0]

    def _segment(self, chunks: tuple[FinalizedChunk, ...]) -> SummarySegment:
        words = _flatten_words(chunks)
        _, average_pace = self._calculate_pace_metrics(words)
        return SummarySegment(
            text=" ".join(chunk.text.strip() for chunk in chunks if chunk.text.strip()),
            average_speaking_pace=average_pace,
            pace_status=classify_pace(average_pace),
        )

    def _calculate_pace_metrics(
        self, words: tuple[RecognizedWord, ...]
    ) -> tuple[float, float | None]:
        active_seconds = self._policy.active_speech_seconds(words)
        average_pace = self._policy.calculate_pace(len(words), active_seconds)
        return active_seconds, average_pace


def _flatten_words(chunks: Iterable[FinalizedChunk]) -> tuple[RecognizedWord, ...]:
    return tuple(word for chunk in chunks for word in chunk.words)
