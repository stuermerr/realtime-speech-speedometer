"""Provider-independent active-speech pace measurement."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal


PaceStatus = Literal["green", "red"]


def classify_pace(wpm: float | None) -> PaceStatus | None:
    """Classify an available raw pace against the inclusive target range."""
    if wpm is None:
        return None
    return "green" if 115.0 <= wpm <= 150.0 else "red"


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
    active_speech_seconds: float
    audio_start_seconds: float | None
    audio_end_seconds: float | None


@dataclass(frozen=True, slots=True)
class ActiveSpeechPolicy:
    """Shared active-speech duration and pace-availability policy."""

    pause_threshold_seconds: float = 1.0
    minimum_active_seconds: float = 4.0

    def __post_init__(self) -> None:
        if not all(
            _is_finite_positive(value)
            for value in (self.pause_threshold_seconds, self.minimum_active_seconds)
        ):
            raise ValueError("Active-speech policy values must be finite and positive")

    def active_speech_seconds(self, words: tuple[RecognizedWord, ...]) -> float:
        return _active_speech_duration(words, self.pause_threshold_seconds)

    def calculate_pace(
        self, word_count: int, active_speech_seconds: float
    ) -> float | None:
        # None means "not enough evidence yet"; it is intentionally distinct from
        # zero pace so the UI can keep showing the last trustworthy live value.
        if active_speech_seconds < self.minimum_active_seconds:
            return None
        return word_count * 60 / active_speech_seconds


class ActiveSpeechWpm:
    """Calculate recent active-speech pace from a complete word timeline."""

    def __init__(
        self,
        *,
        window_seconds: float = 10.0,
        policy: ActiveSpeechPolicy | None = None,
    ) -> None:
        active_speech_policy = ActiveSpeechPolicy() if policy is None else policy
        _validate_configuration(
            window_seconds=window_seconds,
            policy=active_speech_policy,
        )
        self._window_seconds = window_seconds
        self._policy = active_speech_policy

    def calculate(self, words: Iterable[RecognizedWord]) -> WpmMeasurement:
        """Validate a complete timeline and calculate its current measurement."""
        timeline = tuple(words)
        self._validate_timeline(timeline)
        selected_words = self._select_suffix(timeline)
        return self._measurement_for(selected_words)

    def _validate_timeline(self, timeline: tuple[RecognizedWord, ...]) -> None:
        previous_start: float | None = None
        for word in timeline:
            _validate_word(word)
            if previous_start is not None and word.start_seconds < previous_start:
                raise ValueError("Words must be ordered by non-decreasing start time")
            previous_start = word.start_seconds

    def _select_suffix(
        self, words: tuple[RecognizedWord, ...]
    ) -> tuple[RecognizedWord, ...]:
        # Walk backward by whole recognized words until the recent active-speech
        # window is full. Network arrival time never participates in this choice.
        start_index = len(words)
        while start_index > 0:
            start_index -= 1
            selected_words = words[start_index:]
            if (
                self._policy.active_speech_seconds(selected_words)
                >= self._window_seconds
            ):
                return selected_words
        return words

    def _measurement_for(self, words: tuple[RecognizedWord, ...]) -> WpmMeasurement:
        if not words:
            return WpmMeasurement(
                wpm=None,
                word_count=0,
                active_speech_seconds=0.0,
                audio_start_seconds=None,
                audio_end_seconds=None,
            )

        active_speech_seconds = self._policy.active_speech_seconds(words)
        wpm = self._policy.calculate_pace(len(words), active_speech_seconds)
        return WpmMeasurement(
            wpm=wpm,
            word_count=len(words),
            active_speech_seconds=active_speech_seconds,
            audio_start_seconds=words[0].start_seconds,
            audio_end_seconds=max(word.end_seconds for word in words),
        )


class DualWindowActiveSpeechWpm:
    """Blend two stateless active-speech measurements from one timeline.

    The returned measurement deliberately retains the long-window evidence
    metadata for the existing live protocol. Its WPM is the authoritative
    blended live pace and is not derived from those legacy metadata fields.
    """

    def __init__(
        self,
        *,
        short_window_seconds: float,
        long_window_seconds: float,
        short_weight: float,
        policy: ActiveSpeechPolicy | None = None,
    ) -> None:
        active_speech_policy = ActiveSpeechPolicy() if policy is None else policy
        if not _is_finite_number(short_weight) or not 0.0 <= short_weight <= 1.0:
            raise ValueError(
                "Short window weight must be finite and between zero and one"
            )
        if short_window_seconds > long_window_seconds:
            raise ValueError("Short active-speech window cannot exceed the long window")
        self._short = ActiveSpeechWpm(
            window_seconds=short_window_seconds,
            policy=active_speech_policy,
        )
        self._long = ActiveSpeechWpm(
            window_seconds=long_window_seconds,
            policy=active_speech_policy,
        )
        self._short_weight = short_weight

    def calculate(self, words: Iterable[RecognizedWord]) -> WpmMeasurement:
        """Calculate both windows afresh from the complete current timeline."""
        timeline = tuple(words)
        short_measurement = self._short.calculate(timeline)
        long_measurement = self._long.calculate(timeline)
        if short_measurement.wpm is None or long_measurement.wpm is None:
            return long_measurement
        # The short window supplies responsiveness; the long window damps the
        # volatility seen in real microphone trials.
        blended_wpm = (
            self._short_weight * short_measurement.wpm
            + (1.0 - self._short_weight) * long_measurement.wpm
        )
        return replace(long_measurement, wpm=blended_wpm)


def _validate_configuration(
    *,
    window_seconds: float,
    policy: ActiveSpeechPolicy,
) -> None:
    if not _is_finite_positive(window_seconds):
        raise ValueError("WPM window must be finite and positive")
    if policy.minimum_active_seconds > window_seconds:
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
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _active_speech_duration(
    words: tuple[RecognizedWord, ...], pause_threshold_seconds: float
) -> float:
    if not words:
        return 0.0

    active_speech_seconds = 0.0
    interval_end = words[0].end_seconds
    active_speech_seconds += interval_end - words[0].start_seconds
    for word in words[1:]:
        # Union overlapping word intervals so one instant of audio is counted once.
        if word.start_seconds <= interval_end:
            if word.end_seconds > interval_end:
                active_speech_seconds += word.end_seconds - interval_end
                interval_end = word.end_seconds
            continue

        gap = word.start_seconds - interval_end
        # Brief within-phrase gaps are speech rhythm; long pauses are excluded so
        # silence cannot make the speaker appear artificially slow.
        if gap < pause_threshold_seconds:
            active_speech_seconds += gap
        active_speech_seconds += word.end_seconds - word.start_seconds
        interval_end = word.end_seconds
    return active_speech_seconds
