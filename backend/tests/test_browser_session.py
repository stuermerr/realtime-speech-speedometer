from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine, Mapping, Sequence
from typing import Any, TypeVar

import pytest

from app.services.browser_session import BrowserLiveWpmSession, LiveWpmDiagnostics
from app.services.wpm import ActiveSpeechPolicy, ActiveSpeechWpm


Result = TypeVar("Result")


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


def provider_result(
    word_count: int, *, duration: float = 4.0, is_final: bool = False
) -> dict[str, object]:
    word_duration = duration / word_count
    texts = [f"word-{index}" for index in range(word_count)]
    words = [
        {
            "word": text,
            "start": index * word_duration,
            "end": (index + 1) * word_duration,
        }
        for index, text in enumerate(texts)
    ]
    return {
        "type": "Results",
        "is_final": is_final,
        "channel": {"alternatives": [{"transcript": " ".join(texts), "words": words}]},
    }


class FakeBrowser:
    def __init__(self, incoming: Sequence[dict[str, object]]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict[str, object]] = []

    async def receive(self) -> dict[str, object]:
        await asyncio.sleep(0)
        return self.incoming.pop(0)

    async def send_json(self, message: object) -> None:
        assert isinstance(message, dict)
        self.sent.append(message)


class BlockingBrowser(FakeBrowser):
    def __init__(self, incoming: Sequence[dict[str, object]]) -> None:
        super().__init__(incoming)
        self.cancel_count = 0

    async def receive(self) -> dict[str, object]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_count += 1
            raise
        raise AssertionError("unreachable")


class FailingSendBrowser(BlockingBrowser):
    async def send_json(self, message: object) -> None:
        raise OSError("browser closed with provider-secret")


class InactivityBrowser(FakeBrowser):
    def __init__(self) -> None:
        super().__init__([])
        self.stop_requested = asyncio.Event()

    async def receive(self) -> dict[str, object]:
        await self.stop_requested.wait()
        return {"type": "websocket.receive", "text": '{"type":"stop"}'}

    async def send_json(self, message: object) -> None:
        await super().send_json(message)
        if message == {"type": "stop_requested", "reason": "inactivity"}:
            self.stop_requested.set()


class FakeProvider:
    def __init__(
        self,
        events: Sequence[Mapping[str, object]],
        *,
        wait_for_stop: bool = True,
        hang_after_events: bool = False,
    ) -> None:
        self.events_after_stop = list(events)
        self.wait_for_stop = wait_for_stop
        self.hang_after_events = hang_after_events
        self.audio: list[bytes] = []
        self.close_stream_count = 0
        self.close_count = 0
        self.event_stream_cancel_count = 0
        self.close_stream_called = asyncio.Event()

    async def __aenter__(self) -> FakeProvider:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.close_count += 1

    async def send_audio(self, audio: bytes) -> None:
        self.audio.append(audio)

    async def close_stream(self) -> None:
        self.close_stream_count += 1
        self.close_stream_called.set()

    async def provider_events(self) -> AsyncIterator[Mapping[str, object]]:
        try:
            if self.wait_for_stop:
                await self.close_stream_called.wait()
            for event in self.events_after_stop:
                yield event
            if self.hang_after_events:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.event_stream_cancel_count += 1
            raise


def test_binary_audio_and_stop_drain_to_measurements_then_stopped() -> None:
    browser = FakeBrowser(
        [
            {"type": "websocket.receive", "bytes": b"webm-opus"},
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "stop"}),
            },
        ]
    )
    provider = FakeProvider(
        [provider_result(8, is_final=True), {"type": "Metadata", "request_id": "safe"}]
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert provider.audio == [b"webm-opus"]
    assert provider.close_stream_count == 1
    assert provider.close_count == 1
    assert browser.sent == [
        {
            "type": "measurement",
            "wpm": 120.0,
            "pace_status": "green",
            "word_count": 8,
            "active_speech_seconds": 4.0,
            "audio_start_seconds": 0.0,
            "audio_end_seconds": 4.0,
        },
        {
            "type": "summary",
            "average_speaking_pace": 120.0,
            "finalized_words": 8,
            "active_speaking_seconds": 4.0,
            "presentation_duration_seconds": 4.0,
            "segments": [
                {
                    "text": "word-0 word-1 word-2 word-3 word-4 word-5 word-6 word-7",
                    "average_speaking_pace": 120.0,
                    "pace_status": "green",
                }
            ],
        },
        {"type": "stopped", "reason": "user"},
    ]


def test_unavailable_measurement_is_null_and_unchanged_result_is_suppressed() -> None:
    short_result = provider_result(1, duration=0.5)
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider(
        [short_result, short_result, {"type": "Metadata", "request_id": "safe"}]
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == [
        {
            "type": "measurement",
            "wpm": None,
            "pace_status": None,
            "word_count": 1,
            "active_speech_seconds": 0.5,
            "audio_start_seconds": 0.0,
            "audio_end_seconds": 0.5,
        },
        {
            "type": "summary",
            "average_speaking_pace": None,
            "finalized_words": 0,
            "active_speaking_seconds": 0.0,
            "presentation_duration_seconds": 0.0,
            "segments": [],
        },
        {"type": "stopped", "reason": "user"},
    ]


def test_live_minimum_is_independent_from_fixed_summary_minimum() -> None:
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider(
        [provider_result(3, duration=1.5, is_final=True), {"type": "Metadata"}]
    )
    calculator = ActiveSpeechWpm(
        window_seconds=2.0,
        policy=ActiveSpeechPolicy(minimum_active_seconds=1.0),
    )

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            live_wpm_calculator=calculator,
            drain_timeout_seconds=0.5,
        ).run()
    )

    assert browser.sent[0]["wpm"] == 120.0
    assert browser.sent[1]["average_speaking_pace"] is None


def test_inactivity_requests_browser_stop_then_emits_empty_completion() -> None:
    browser = InactivityBrowser()
    provider = FakeProvider([{"type": "Metadata", "request_id": "safe"}])

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            inactivity_timeout_seconds=0.01,
            stop_ack_timeout_seconds=0.5,
            drain_timeout_seconds=0.5,
        ).run()
    )

    assert browser.sent == [
        {"type": "stop_requested", "reason": "inactivity"},
        {
            "type": "summary",
            "average_speaking_pace": None,
            "finalized_words": 0,
            "active_speaking_seconds": 0.0,
            "presentation_duration_seconds": 0.0,
            "segments": [],
        },
        {"type": "stopped", "reason": "inactivity"},
    ]


def test_missing_inactivity_stop_acknowledgement_is_a_safe_error() -> None:
    browser = BlockingBrowser([])
    provider = FakeProvider([], hang_after_events=True)

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            inactivity_timeout_seconds=0.01,
            stop_ack_timeout_seconds=0.01,
            drain_timeout_seconds=0.5,
        ).run()
    )

    assert browser.sent == [
        {"type": "stop_requested", "reason": "inactivity"},
        {"type": "error", "message": "Session protocol error"},
    ]
    assert browser.cancel_count == 1
    assert provider.event_stream_cancel_count == 1
    assert provider.close_count == 1


def test_invalid_control_is_fatal_safe_and_cancels_provider_flow() -> None:
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"unknown"}'}])
    provider = FakeProvider([], hang_after_events=True)

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == [{"type": "error", "message": "Session protocol error"}]
    assert provider.close_stream_count == 0
    assert provider.event_stream_cancel_count == 1
    assert provider.close_count == 1


@pytest.mark.parametrize(
    "message",
    [
        {"type": "websocket.receive", "bytes": b""},
        {"type": "websocket.receive", "text": "not-json"},
        {"type": "websocket.receive", "text": "[]"},
        {
            "type": "websocket.receive",
            "text": '{"type":"stop","extra":true}',
        },
        {"type": "websocket.receive", "text": '{"type":"STOP"}'},
    ],
)
def test_empty_audio_and_malformed_controls_are_fatal(
    message: dict[str, object],
) -> None:
    browser = FakeBrowser([message])
    provider = FakeProvider([], hang_after_events=True)

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == [{"type": "error", "message": "Session protocol error"}]
    assert provider.audio == []
    assert provider.close_stream_count == 0
    assert provider.close_count == 1


def test_browser_disconnect_cancels_provider_without_waiting_for_drain() -> None:
    browser = FakeBrowser([{"type": "websocket.disconnect", "code": 1001}])
    provider = FakeProvider([], hang_after_events=True)

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == []
    assert provider.close_stream_count == 0
    assert provider.event_stream_cancel_count == 1
    assert provider.close_count == 1


def test_provider_failure_cancels_browser_and_sends_safe_error() -> None:
    browser = BlockingBrowser([])
    provider = FakeProvider(
        [{"type": "Error", "description": "provider-secret"}],
        wait_for_stop=False,
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == [{"type": "error", "message": "Live transcription failed"}]
    assert "provider-secret" not in str(browser.sent)
    assert provider.close_count == 1


def test_normal_provider_close_before_stop_is_an_error_and_cancels_browser() -> None:
    browser = BlockingBrowser([])
    provider = FakeProvider(
        [{"type": "Metadata", "request_id": "safe"}],
        wait_for_stop=False,
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == [{"type": "error", "message": "Session protocol error"}]
    assert browser.cancel_count == 1
    assert provider.close_count == 1


def test_missing_metadata_after_stop_is_an_error_not_stopped() -> None:
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider([provider_result(8)])

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent[-1] == {
        "type": "error",
        "message": "Session protocol error",
    }
    assert {message["type"] for message in browser.sent} == {"measurement", "error"}
    assert provider.close_count == 1


def test_provider_drain_timeout_is_error_and_cleans_up_once() -> None:
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider(
        [{"type": "Metadata", "request_id": "safe"}],
        hang_after_events=True,
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.01).run())

    assert browser.sent == [
        {"type": "error", "message": "Transcription did not finish in time"}
    ]
    assert provider.close_stream_count == 1
    assert provider.event_stream_cancel_count == 1
    assert provider.close_count == 1


def test_browser_send_failure_cancels_active_provider_and_cleans_up_once() -> None:
    browser = FailingSendBrowser([])
    provider = FakeProvider(
        [provider_result(8)], wait_for_stop=False, hang_after_events=True
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.cancel_count == 1
    assert provider.close_count == 1


def test_live_wpm_diagnostics_emit_nothing_when_disabled() -> None:
    records: list[str] = []
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider([{"type": "Metadata", "request_id": "safe"}])
    diagnostics = LiveWpmDiagnostics(enabled=False, sink=records.append)

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            diagnostics=diagnostics,
            drain_timeout_seconds=0.5,
        ).run()
    )

    assert records == []


def test_live_wpm_diagnostics_cover_normal_pipeline_without_sensitive_content() -> None:
    canaries = (
        "audio-canary",
        "transcript-canary",
        "word-canary",
        "authorization-canary",
        "device-canary",
    )
    audio = canaries[0].encode()
    short_result = {
        "type": "Results",
        "is_final": False,
        "authorization": canaries[3],
        "channel": {
            "alternatives": [
                {
                    "transcript": canaries[1],
                    "words": [{"word": canaries[2], "start": 0.0, "end": 0.5}],
                }
            ]
        },
    }
    browser = FakeBrowser(
        [
            {"type": "websocket.receive", "bytes": audio},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    provider = FakeProvider(
        [
            short_result,
            short_result,
            provider_result(8),
            {
                "type": "Metadata",
                "request_id": canaries[4],
            },
        ]
    )
    output: list[str] = []
    diagnostics = LiveWpmDiagnostics(
        enabled=True,
        sink=output.append,
        clock=lambda: 100.0,
        session_id="session-safe",
    )

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            diagnostics=diagnostics,
            drain_timeout_seconds=0.5,
        ).run()
    )

    records = [json.loads(line) for line in output]
    started = next(
        record
        for record in records
        if record["stage"] == "session" and record["event"] == "started"
    )
    events = {(record["stage"], record["event"]) for record in records}
    assert {
        ("browser", "audio_received"),
        ("provider", "audio_forwarded"),
        ("browser", "stop_received"),
        ("provider", "close_stream_sent"),
        ("provider", "event_received"),
        ("pipeline", "timeline_changed"),
        ("pipeline", "wpm_availability"),
        ("browser", "measurement_sent"),
        ("browser", "measurement_suppressed"),
        ("provider", "stream_closed"),
        ("browser", "stopped_sent"),
        ("session", "cleanup_finished"),
    } <= events
    assert all(record["session_id"] == "session-safe" for record in records)
    assert all(record["relative_seconds"] == 0.0 for record in records)

    audio_record = next(
        record for record in records if record["event"] == "audio_received"
    )
    assert audio_record["chunk_index"] == 1
    assert audio_record["byte_size"] == len(audio)

    result_records = [
        record
        for record in records
        if record["event"] == "event_received"
        and record["provider_event_type"] == "Results"
    ]
    assert (
        result_records[0]
        | {
            "is_final": False,
            "word_count": 1,
            "audio_start_seconds": 0.0,
            "audio_end_seconds": 0.5,
        }
        == result_records[0]
    )
    assert {
        record["available"]
        for record in records
        if record["event"] == "wpm_availability"
    } == {False, True}
    assert {
        record["changed"] for record in records if record["event"] == "timeline_changed"
    } == {False, True}
    assert all(canary not in "\n".join(output) for canary in canaries)
    assert "live_window_seconds" not in started
    assert "live_minimum_active_seconds" not in started


def test_live_wpm_diagnostics_include_effective_tuning_when_enabled() -> None:
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider([{"type": "Metadata", "request_id": "safe"}])
    output: list[str] = []

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            diagnostics=LiveWpmDiagnostics(
                enabled=True,
                sink=output.append,
                live_window_seconds=6.0,
                live_minimum_active_seconds=3.0,
            ),
            drain_timeout_seconds=0.5,
        ).run()
    )

    started = json.loads(output[0])
    assert started["live_window_seconds"] == 6.0
    assert started["live_minimum_active_seconds"] == 3.0


def test_live_wpm_diagnostics_categorize_abnormal_provider_close_safely() -> None:
    canary = "provider-error-canary"
    browser = BlockingBrowser([])
    provider = FakeProvider(
        [{"type": "Error", "description": canary}],
        wait_for_stop=False,
    )
    output: list[str] = []

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            diagnostics=LiveWpmDiagnostics(enabled=True, sink=output.append),
            drain_timeout_seconds=0.5,
        ).run()
    )

    records = [json.loads(line) for line in output]
    assert any(
        record["stage"] == "provider"
        and record["event"] == "event_received"
        and record["provider_event_type"] == "Error"
        for record in records
    )
    assert any(
        record["stage"] == "provider"
        and record["event"] == "stream_closed"
        and record["outcome"] == "abnormal"
        for record in records
    )
    assert any(
        record["stage"] == "session"
        and record["event"] == "failed"
        and record["category"] == "provider"
        for record in records
    )
    assert canary not in "\n".join(output)


def test_live_wpm_diagnostics_show_timeout_cancellation_and_cleanup() -> None:
    browser = FakeBrowser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    provider = FakeProvider(
        [{"type": "Metadata", "request_id": "safe"}],
        hang_after_events=True,
    )
    output: list[str] = []

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            diagnostics=LiveWpmDiagnostics(enabled=True, sink=output.append),
            drain_timeout_seconds=0.01,
        ).run()
    )

    records = [json.loads(line) for line in output]
    events = {
        (record["stage"], record["event"], record.get("outcome")) for record in records
    }
    assert ("provider", "stream_closed", "cancelled") in events
    assert ("provider", "drain_timeout", None) in events
    assert ("session", "cleanup_started", None) in events
    assert ("session", "cleanup_finished", None) in events


def test_live_wpm_diagnostics_show_browser_flow_cancellation() -> None:
    browser = BlockingBrowser([])
    provider = FakeProvider(
        [{"type": "Metadata", "request_id": "safe"}],
        wait_for_stop=False,
    )
    output: list[str] = []

    run(
        BrowserLiveWpmSession(
            browser,
            provider,
            diagnostics=LiveWpmDiagnostics(enabled=True, sink=output.append),
            drain_timeout_seconds=0.5,
        ).run()
    )

    records = [json.loads(line) for line in output]
    assert any(
        record["stage"] == "browser" and record["event"] == "flow_cancelled"
        for record in records
    )
