from app.services.session_summary import FinalizedChunk, SessionSummaryCalculator
from app.services.wpm import ActiveSpeechPolicy, RecognizedWord


def word(index: int, start: float, end: float) -> RecognizedWord:
    return RecognizedWord(f"word-{index}", start, end)


def test_summary_uses_the_complete_finalized_timeline_not_live_measurements() -> None:
    summary = SessionSummaryCalculator().build(
        [FinalizedChunk("words", (
            word(0, 2.0, 2.5),
            word(1, 3.0, 3.5),
            word(2, 4.0, 4.5),
            word(3, 5.0, 5.5),
            word(4, 8.0, 8.5),
        ))]
    )

    assert summary.finalized_words == 5
    assert summary.active_speaking_seconds == 4.0
    assert summary.average_speaking_pace == 75.0
    assert summary.presentation_duration_seconds == 6.5


def test_summary_uses_the_shared_active_speech_policy_for_all_pace_views() -> None:
    policy = ActiveSpeechPolicy(
        pause_threshold_seconds=0.75,
        minimum_active_seconds=1.5,
    )

    summary = SessionSummaryCalculator(policy=policy).build(
        (
            FinalizedChunk("First", (word(0, 0.0, 0.5),)),
            FinalizedChunk("second", (word(1, 1.0, 1.5),)),
        )
    )

    assert summary.active_speaking_seconds == 1.5
    assert summary.average_speaking_pace == 80.0
    assert [segment.average_speaking_pace for segment in summary.segments] == [80.0]


def test_short_and_empty_summaries_are_honest() -> None:
    calculator = SessionSummaryCalculator()

    empty = calculator.build([])
    short = calculator.build([FinalizedChunk("word", (word(0, 7.0, 7.5),))])

    assert empty.finalized_words == 0
    assert empty.active_speaking_seconds == 0.0
    assert empty.presentation_duration_seconds == 0.0
    assert empty.average_speaking_pace is None
    assert short.finalized_words == 1
    assert short.active_speaking_seconds == 0.5
    assert short.presentation_duration_seconds == 0.5
    assert short.average_speaking_pace is None


def test_segments_group_final_chunks_and_merge_a_short_remainder_backward() -> None:
    chunks = (
        FinalizedChunk("  First,  ", (word(0, 0.0, 2.0),)),
        FinalizedChunk("chunk.  ", (word(1, 2.0, 4.0),)),
        FinalizedChunk("Second", (word(2, 6.0, 8.0),)),
        FinalizedChunk("chunk!", (word(3, 8.0, 10.0),)),
        FinalizedChunk("Tail?", (word(4, 12.0, 12.5),)),
    )

    summary = SessionSummaryCalculator().build(chunks)

    assert summary.finalized_words == 5
    assert summary.active_speaking_seconds == 8.5
    assert [(segment.text, segment.average_speaking_pace, segment.pace_status) for segment in summary.segments] == [
        ("First, chunk.", 30.0, "red"),
        ("Second chunk! Tail?", 40.0, "red"),
    ]


def test_short_presentation_has_one_unavailable_segment_and_empty_has_none() -> None:
    calculator = SessionSummaryCalculator()

    short = calculator.build((FinalizedChunk(" Short. ", (word(0, 4.0, 4.5),)),))
    empty = calculator.build(())

    assert [(segment.text, segment.average_speaking_pace, segment.pace_status) for segment in short.segments] == [
        ("Short.", None, None),
    ]
    assert empty.segments == ()


def test_segment_boundary_gaps_are_independent_from_global_active_duration() -> None:
    chunks = (
        FinalizedChunk("First", (word(0, 0.0, 4.0),)),
        FinalizedChunk("Second", (word(1, 4.5, 8.5),)),
    )

    summary = SessionSummaryCalculator().build(chunks)

    assert summary.active_speaking_seconds == 8.5
    assert [segment.average_speaking_pace for segment in summary.segments] == [15.0, 15.0]
