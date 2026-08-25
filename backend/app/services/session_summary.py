"""Deterministic global metrics for one finalized presentation timeline."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from app.services.wpm import RecognizedWord, active_speech_duration


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Unrounded quantitative metrics calculated from finalized words only."""

    average_speaking_pace: float | None
    finalized_words: int
    active_speaking_seconds: float
    presentation_duration_seconds: float


class SessionSummaryCalculator:
    """Build one global summary with the established active-speech policy."""

    def __init__(
        self,
        *,
        pause_threshold_seconds: float = 1.0,
        minimum_active_seconds: float = 4.0,
    ) -> None:
        self._pause_threshold_seconds = pause_threshold_seconds
        self._minimum_active_seconds = minimum_active_seconds

    def build(self, finalized_words: Iterable[RecognizedWord]) -> SessionSummary:
        """Calculate global metrics from the immutable finalized timeline."""
        words = tuple(finalized_words)
        if not words:
            return SessionSummary(None, 0, 0.0, 0.0)

        active_seconds = active_speech_duration(words, self._pause_threshold_seconds)
        average_pace = None
        if active_seconds >= self._minimum_active_seconds:
            average_pace = len(words) * 60 / active_seconds
        return SessionSummary(
            average_speaking_pace=average_pace,
            finalized_words=len(words),
            active_speaking_seconds=active_seconds,
            presentation_duration_seconds=(
                max(word.end_seconds for word in words) - words[0].start_seconds
            ),
        )
