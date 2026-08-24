"""Provider-independent active-speech pace measurement."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecognizedWord:
    """A provider-normalized word positioned on the audio timeline."""

    text: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class WpmMeasurement:
    """The current whole-word suffix and its active-speech pace."""

    wpm: float | None
    word_count: int
    active_seconds: float
    audio_start_seconds: float | None
    audio_end_seconds: float | None


class ActiveSpeechWpm:
    """Maintain the recent recognized-word suffix used for current WPM."""

    def __init__(
        self,
        *,
        window_seconds: float = 10.0,
        pause_threshold_seconds: float = 1.0,
        minimum_active_seconds: float = 4.0,
    ) -> None:
        _validate_configuration(
            window_seconds=window_seconds,
            pause_threshold_seconds=pause_threshold_seconds,
            minimum_active_seconds=minimum_active_seconds,
        )
        self._window_seconds = window_seconds
        self._pause_threshold_seconds = pause_threshold_seconds
        self._minimum_active_seconds = minimum_active_seconds
        self._words: tuple[RecognizedWord, ...] = ()

    def add_words(self, words: Iterable[RecognizedWord]) -> WpmMeasurement:
        """Atomically add a chronological batch and return the current measurement."""
        batch = tuple(words)
        self._validate_batch(batch)
        if not batch:
            return self._measurement_for(self._words)

        selected_words = self._select_suffix((*self._words, *batch))
        self._words = selected_words
        return self._measurement_for(selected_words)

    def _validate_batch(self, batch: tuple[RecognizedWord, ...]) -> None:
        previous_start = self._words[-1].start_seconds if self._words else None
        for word in batch:
            _validate_word(word)
            if previous_start is not None and word.start_seconds < previous_start:
                raise ValueError("Words must be ordered by non-decreasing start time")
            previous_start = word.start_seconds

    def _select_suffix(
        self, words: tuple[RecognizedWord, ...]
    ) -> tuple[RecognizedWord, ...]:
        start_index = len(words)
        while start_index > 0:
            start_index -= 1
            selected_words = words[start_index:]
            if (
                _active_duration(selected_words, self._pause_threshold_seconds)
                >= self._window_seconds
            ):
                return selected_words
        return words

    def _measurement_for(self, words: tuple[RecognizedWord, ...]) -> WpmMeasurement:
        if not words:
            return WpmMeasurement(
                wpm=None,
                word_count=0,
                active_seconds=0.0,
                audio_start_seconds=None,
                audio_end_seconds=None,
            )

        active_seconds = _active_duration(words, self._pause_threshold_seconds)
        wpm = None
        if active_seconds >= self._minimum_active_seconds:
            wpm = len(words) * 60 / active_seconds
        return WpmMeasurement(
            wpm=wpm,
            word_count=len(words),
            active_seconds=active_seconds,
            audio_start_seconds=words[0].start_seconds,
            audio_end_seconds=max(word.end_seconds for word in words),
        )


def _validate_configuration(
    *,
    window_seconds: float,
    pause_threshold_seconds: float,
    minimum_active_seconds: float,
) -> None:
    values = (window_seconds, pause_threshold_seconds, minimum_active_seconds)
    if not all(_is_finite_positive(value) for value in values):
        raise ValueError("WPM configuration values must be finite and positive")
    if minimum_active_seconds > window_seconds:
        raise ValueError("Minimum active speech cannot exceed the active-speech window")


def _validate_word(word: RecognizedWord) -> None:
    if not isinstance(word.text, str) or not word.text.strip():
        raise ValueError("Recognized words must contain non-blank text")
    if not (
        _is_finite_number(word.start_seconds)
        and _is_finite_number(word.end_seconds)
        and word.start_seconds >= 0
        and word.end_seconds > word.start_seconds
    ):
        raise ValueError("Word timestamps must be finite, non-negative, and increasing")


def _is_finite_positive(value: float) -> bool:
    return _is_finite_number(value) and value > 0


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _active_duration(
    words: tuple[RecognizedWord, ...], pause_threshold_seconds: float
) -> float:
    if not words:
        return 0.0

    active_seconds = 0.0
    interval_end = words[0].end_seconds
    active_seconds += interval_end - words[0].start_seconds
    for word in words[1:]:
        if word.start_seconds <= interval_end:
            if word.end_seconds > interval_end:
                active_seconds += word.end_seconds - interval_end
                interval_end = word.end_seconds
            continue

        gap = word.start_seconds - interval_end
        if gap < pause_threshold_seconds:
            active_seconds += gap
        active_seconds += word.end_seconds - word.start_seconds
        interval_end = word.end_seconds
    return active_seconds
