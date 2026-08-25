from app.services.session_summary import SessionSummaryCalculator
from app.services.wpm import RecognizedWord


def word(index: int, start: float, end: float) -> RecognizedWord:
    return RecognizedWord(f"word-{index}", start, end)


def test_summary_uses_the_complete_finalized_timeline_not_live_measurements() -> None:
    summary = SessionSummaryCalculator().build(
        [
            word(0, 2.0, 2.5),
            word(1, 3.0, 3.5),
            word(2, 4.0, 4.5),
            word(3, 5.0, 5.5),
            word(4, 8.0, 8.5),
        ]
    )

    assert summary.finalized_words == 5
    assert summary.active_speaking_seconds == 4.0
    assert summary.average_speaking_pace == 75.0
    assert summary.presentation_duration_seconds == 6.5


def test_short_and_empty_summaries_are_honest() -> None:
    calculator = SessionSummaryCalculator()

    empty = calculator.build([])
    short = calculator.build([word(0, 7.0, 7.5)])

    assert empty.finalized_words == 0
    assert empty.active_speaking_seconds == 0.0
    assert empty.presentation_duration_seconds == 0.0
    assert empty.average_speaking_pace is None
    assert short.finalized_words == 1
    assert short.active_speaking_seconds == 0.5
    assert short.presentation_duration_seconds == 0.5
    assert short.average_speaking_pace is None
