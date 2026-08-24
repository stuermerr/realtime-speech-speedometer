from __future__ import annotations

import math

import pytest

from app.services.wpm import ActiveSpeechWpm, RecognizedWord


def word(index: int, start: float, end: float) -> RecognizedWord:
    return RecognizedWord(text=f"word-{index}", start_seconds=start, end_seconds=end)


def test_continuous_speech_reports_unrounded_wpm() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [word(index, index * 0.5, (index + 1) * 0.5) for index in range(8)]
    )

    assert measurement.word_count == 8
    assert measurement.active_seconds == 4.0
    assert measurement.wpm == 120.0
    assert measurement.audio_start_seconds == 0.0
    assert measurement.audio_end_seconds == 4.0


def test_fast_speech_reports_high_wpm() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [word(index, index * 0.25, (index + 1) * 0.25) for index in range(16)]
    )

    assert measurement.active_seconds == 4.0
    assert measurement.wpm == 240.0


def test_short_gaps_count_as_active_speech() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [word(index, index * 1.0, index * 1.0 + 0.5) for index in range(5)]
    )

    assert measurement.active_seconds == 4.5
    assert measurement.wpm == pytest.approx(66.66666666666667)


def test_long_pause_keeps_pre_pause_speech_until_window_advances() -> None:
    meter = ActiveSpeechWpm()
    before_pause = [word(index, index * 0.5, (index + 1) * 0.5) for index in range(6)]
    after_pause = [word(index + 6, 10 + index * 0.5, 10 + (index + 1) * 0.5) for index in range(4)]

    paused_measurement = meter.add_words([*before_pause, *after_pause])
    advanced_measurement = meter.add_words(
        [word(index + 10, 12 + index * 0.5, 12 + (index + 1) * 0.5) for index in range(16)]
    )

    assert paused_measurement.word_count == 10
    assert paused_measurement.active_seconds == 5.0
    assert paused_measurement.wpm == 120.0
    assert advanced_measurement.word_count == 20
    assert advanced_measurement.active_seconds == 10.0
    assert advanced_measurement.audio_start_seconds == 10.0


def test_window_keeps_complete_words_and_can_exceed_its_target() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [word(index, index * 3.0, (index + 1) * 3.0) for index in range(4)]
    )

    assert measurement.word_count == 4
    assert measurement.active_seconds == 12.0
    assert measurement.audio_start_seconds == 0.0
    assert measurement.wpm == 20.0


def test_window_rolls_forward_and_prunes_irrelevant_history() -> None:
    meter = ActiveSpeechWpm()

    meter.add_words([word(index, index * 0.5, (index + 1) * 0.5) for index in range(22)])
    measurement = meter.add_words([word(22, 11.0, 11.5)])

    assert measurement.word_count == 20
    assert measurement.active_seconds == 10.0
    assert measurement.audio_start_seconds == 1.5
    assert measurement.audio_end_seconds == 11.5


def test_exact_pause_threshold_is_excluded_from_active_speech() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [word(0, 0.0, 2.0), word(1, 3.0, 5.0)]
    )

    assert measurement.active_seconds == 4.0
    assert measurement.wpm == 30.0


def test_overlapping_words_use_their_union_as_active_duration() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [
            word(0, 0.0, 1.0),
            word(1, 0.8, 1.8),
            word(2, 1.8, 2.8),
            word(3, 2.8, 3.8),
            word(4, 3.8, 4.8),
        ]
    )

    assert measurement.active_seconds == 4.8
    assert measurement.wpm == 62.5


def test_empty_and_one_word_states_have_no_wpm() -> None:
    meter = ActiveSpeechWpm()

    empty_measurement = meter.add_words([])
    one_word_measurement = meter.add_words([word(0, 7.0, 7.5)])

    assert empty_measurement.wpm is None
    assert empty_measurement.word_count == 0
    assert empty_measurement.active_seconds == 0.0
    assert empty_measurement.audio_start_seconds is None
    assert empty_measurement.audio_end_seconds is None
    assert one_word_measurement.wpm is None
    assert one_word_measurement.word_count == 1
    assert one_word_measurement.active_seconds == 0.5
    assert one_word_measurement.audio_start_seconds == 7.0
    assert one_word_measurement.audio_end_seconds == 7.5


@pytest.mark.parametrize(
    ("kwargs"),
    [
        {"window_seconds": 0.0},
        {"pause_threshold_seconds": math.inf},
        {"minimum_active_seconds": math.nan},
        {"window_seconds": 3.0, "minimum_active_seconds": 4.0},
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ActiveSpeechWpm(**kwargs)


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
        meter.add_words([invalid_word])


def test_batches_require_non_decreasing_starts_and_fail_atomically() -> None:
    meter = ActiveSpeechWpm()
    original = meter.add_words([word(0, 0.0, 2.0), word(1, 2.0, 4.0)])

    with pytest.raises(ValueError):
        meter.add_words([word(2, 4.0, 5.0), word(3, 3.0, 4.0)])

    assert meter.add_words([]) == original


def test_batches_accept_slightly_overlapping_adjacent_words() -> None:
    meter = ActiveSpeechWpm()

    measurement = meter.add_words(
        [word(0, 0.0, 2.0), word(1, 1.9, 4.0)]
    )

    assert measurement.active_seconds == 4.0
    assert measurement.wpm == 30.0
