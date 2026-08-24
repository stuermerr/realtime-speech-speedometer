from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.wpm import ActiveSpeechWpm, RecognizedWord


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "deepgram_timing_words.json"


def test_captured_word_timing_preserves_active_speech_wpm_behavior() -> None:
    records = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(records) == 46
    assert all(set(record) == {"text", "start", "end"} for record in records)

    words = [
        RecognizedWord(
            text=record["text"],
            start_seconds=record["start"],
            end_seconds=record["end"],
        )
        for record in records
    ]
    meter = ActiveSpeechWpm()

    before_long_pause = meter.add_words(words[:24])
    after_long_pause = meter.add_words(words[24:32])
    rolled_window = meter.add_words(words[32:])

    assert before_long_pause.word_count == 24
    assert before_long_pause.active_seconds == pytest.approx(8.31)
    assert before_long_pause.wpm == pytest.approx(173.2851985559567)
    assert before_long_pause.audio_start_seconds == 0.16
    assert before_long_pause.audio_end_seconds == 8.47

    assert after_long_pause.word_count == 32
    assert after_long_pause.active_seconds == pytest.approx(10.47)
    assert after_long_pause.wpm == pytest.approx(183.3810888252149)
    assert after_long_pause.audio_start_seconds == 0.16
    assert after_long_pause.audio_end_seconds == 15.5

    assert rolled_window.word_count == 36
    assert rolled_window.active_seconds == pytest.approx(10.8)
    assert rolled_window.wpm == pytest.approx(200.0)
    assert rolled_window.audio_start_seconds == 4.15
    assert rolled_window.audio_end_seconds == 20.84
