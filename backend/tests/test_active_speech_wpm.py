from __future__ import annotations

import math

import pytest

from app.services.wpm import (
    ActiveSpeechPolicy,
    ActiveSpeechWpm,
    RecognizedWord,
    classify_pace,
)


def word(index: int, start: float, end: float) -> RecognizedWord:
    return RecognizedWord(text=f"word-{index}", start_seconds=start, end_seconds=end)


def test_active_speech_policy_owns_duration_availability_and_pace() -> None:
    policy = ActiveSpeechPolicy(
        pause_threshold_seconds=0.75,
        minimum_active_seconds=2.0,
    )

    active_seconds = policy.active_speech_seconds(
        (
            word(0, 0.0, 0.5),
            word(1, 1.0, 1.5),
            word(2, 2.5, 3.5),
        )
    )

    assert active_seconds == 2.5
    assert policy.calculate_pace(3, active_seconds) == 72.0
    assert policy.calculate_pace(2, 1.5) is None


def test_live_wpm_uses_the_shared_active_speech_policy() -> None:
    policy = ActiveSpeechPolicy(
        pause_threshold_seconds=0.75,
        minimum_active_seconds=2.0,
    )
    meter = ActiveSpeechWpm(policy=policy)

    measurement = meter.calculate(
        (
            word(0, 0.0, 0.5),
            word(1, 1.0, 1.5),
            word(2, 2.5, 3.5),
        )
    )

    assert measurement.active_speech_seconds == 2.5
    assert measurement.wpm == 72.0


def test_continuous_speech_reports_unrounded_wpm() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(index, index * 0.5, (index + 1) * 0.5) for index in range(8)]
    )

    assert measurement.word_count == 8
    assert measurement.active_speech_seconds == 4.0
    assert measurement.wpm == 120.0
    assert measurement.audio_start_seconds == 0.0
    assert measurement.audio_end_seconds == 4.0


@pytest.mark.parametrize(
    ("wpm", "expected"),
    [
        (None, None),
        (114.9, "red"),
        (115.0, "green"),
        (150.0, "green"),
        (150.4, "red"),
    ],
)
def test_pace_is_classified_from_the_unrounded_measurement(
    wpm: float | None, expected: str | None
) -> None:
    assert classify_pace(wpm) == expected


def test_fast_speech_reports_high_wpm() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(index, index * 0.25, (index + 1) * 0.25) for index in range(16)]
    )

    assert measurement.active_speech_seconds == 4.0
    assert measurement.wpm == 240.0


def test_short_gaps_count_as_active_speech() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(index, index * 1.0, index * 1.0 + 0.5) for index in range(5)]
    )

    assert measurement.active_speech_seconds == 4.5
    assert measurement.wpm == pytest.approx(66.66666666666667)


def test_long_pause_keeps_pre_pause_speech_until_window_advances() -> None:
    meter = ActiveSpeechWpm()
    before_pause = [word(index, index * 0.5, (index + 1) * 0.5) for index in range(6)]
    after_pause = [word(index + 6, 10 + index * 0.5, 10 + (index + 1) * 0.5) for index in range(4)]

    paused_measurement = meter.calculate([*before_pause, *after_pause])
    advanced_measurement = meter.calculate([
        *before_pause,
        *after_pause,
        *[
            word(index + 10, 12 + index * 0.5, 12 + (index + 1) * 0.5)
            for index in range(16)
        ],
    ])

    assert paused_measurement.word_count == 10
    assert paused_measurement.active_speech_seconds == 5.0
    assert paused_measurement.wpm == 120.0
    assert advanced_measurement.word_count == 20
    assert advanced_measurement.active_speech_seconds == 10.0
    assert advanced_measurement.audio_start_seconds == 10.0


def test_window_keeps_complete_words_and_can_exceed_its_target() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(index, index * 3.0, (index + 1) * 3.0) for index in range(4)]
    )

    assert measurement.word_count == 4
    assert measurement.active_speech_seconds == 12.0
    assert measurement.audio_start_seconds == 0.0
    assert measurement.wpm == 20.0


def test_window_rolls_forward_and_prunes_irrelevant_history() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(index, index * 0.5, (index + 1) * 0.5) for index in range(23)]
    )

    assert measurement.word_count == 20
    assert measurement.active_speech_seconds == 10.0
    assert measurement.audio_start_seconds == 1.5
    assert measurement.audio_end_seconds == 11.5


def test_exact_pause_threshold_is_excluded_from_active_speech() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(0, 0.0, 2.0), word(1, 3.0, 5.0)]
    )

    assert measurement.active_speech_seconds == 4.0
    assert measurement.wpm == 30.0


def test_overlapping_words_use_their_union_as_active_duration() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [
            word(0, 0.0, 1.0),
            word(1, 0.8, 1.8),
            word(2, 1.8, 2.8),
            word(3, 2.8, 3.8),
            word(4, 3.8, 4.8),
        ]
    )

    assert measurement.active_speech_seconds == 4.8
    assert measurement.wpm == 62.5


def test_empty_and_one_word_states_have_no_wpm() -> None:
    meter = ActiveSpeechWpm()

    empty_measurement = meter.calculate([])
    one_word_measurement = meter.calculate([word(0, 7.0, 7.5)])

    assert empty_measurement.wpm is None
    assert empty_measurement.word_count == 0
    assert empty_measurement.active_speech_seconds == 0.0
    assert empty_measurement.audio_start_seconds is None
    assert empty_measurement.audio_end_seconds is None
    assert one_word_measurement.wpm is None
    assert one_word_measurement.word_count == 1
    assert one_word_measurement.active_speech_seconds == 0.5
    assert one_word_measurement.audio_start_seconds == 7.0
    assert one_word_measurement.audio_end_seconds == 7.5


def test_invalid_window_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        ActiveSpeechWpm(window_seconds=0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pause_threshold_seconds": math.inf},
        {"minimum_active_seconds": math.nan},
    ],
)
def test_invalid_policy_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ActiveSpeechPolicy(**kwargs)


def test_policy_minimum_cannot_exceed_the_live_window() -> None:
    with pytest.raises(ValueError):
        ActiveSpeechWpm(
            window_seconds=3.0,
            policy=ActiveSpeechPolicy(minimum_active_seconds=4.0),
        )


@pytest.mark.parametrize(
    "invalid_word",
    [
        RecognizedWord(text=" ", start_seconds=0.0, end_seconds=1.0),
        RecognizedWord(text="word", start_seconds=-0.1, end_seconds=1.0),
        RecognizedWord(text="word", start_seconds=1.0, end_seconds=1.0),
        RecognizedWord(text="word", start_seconds=0.0, end_seconds=math.inf),
    ],
)
def test_invalid_words_are_rejected(invalid_word: RecognizedWord) -> None:
    meter = ActiveSpeechWpm()

    with pytest.raises(ValueError):
        meter.calculate([invalid_word])


def test_complete_timeline_requires_non_decreasing_starts() -> None:
    meter = ActiveSpeechWpm()

    with pytest.raises(ValueError):
        meter.calculate([word(0, 0.0, 2.0), word(1, 2.0, 3.0), word(2, 1.0, 4.0)])


def test_timeline_accepts_slightly_overlapping_adjacent_words() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.calculate(
        [word(0, 0.0, 2.0), word(1, 1.9, 4.0)]
    )

    assert measurement.active_speech_seconds == 4.0
    assert measurement.wpm == 30.0


def test_calculation_is_repeatable_for_the_same_complete_timeline() -> None:
    meter = ActiveSpeechWpm()
    timeline = [word(index, index * 0.5, (index + 1) * 0.5) for index in range(8)]

    first = meter.calculate(timeline)
    second = meter.calculate(timeline)

    assert second == first


def test_calculations_are_independent_between_calls() -> None:
    meter = ActiveSpeechWpm()
    meter.calculate(
        [word(index, index * 0.5, (index + 1) * 0.5) for index in range(24)]
    )

    measurement = meter.calculate([word(100, 20.0, 20.5)])

    assert measurement.word_count == 1
    assert measurement.active_speech_seconds == 0.5
    assert measurement.audio_start_seconds == 20.0
    assert measurement.audio_end_seconds == 20.5


def test_invalid_timeline_does_not_affect_later_calculations() -> None:
    meter = ActiveSpeechWpm()
    valid_timeline = [word(index, index * 0.5, (index + 1) * 0.5) for index in range(8)]

    with pytest.raises(ValueError):
        meter.calculate([*valid_timeline, word(99, 2.0, 2.5)])

    assert meter.calculate(valid_timeline) == ActiveSpeechWpm().calculate(valid_timeline)
