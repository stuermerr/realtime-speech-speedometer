from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine, Mapping, Sequence
from typing import Any, TypeVar

import pytest

from app.services.browser_session import BrowserLiveWpmSession


Result = TypeVar("Result")


def run(coroutine: Coroutine[Any, Any, Result]) -> Result:
    return asyncio.run(coroutine)


def provider_result(word_count: int, *, duration: float = 4.0) -> dict[str, object]:
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
        "is_final": False,
        "channel": {
            "alternatives": [
                {"transcript": " ".join(texts), "words": words}
            ]
        },
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
        [provider_result(8), {"type": "Metadata", "request_id": "safe"}]
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert provider.audio == [b"webm-opus"]
    assert provider.close_stream_count == 1
    assert provider.close_count == 1
    assert browser.sent == [
        {
            "type": "measurement",
            "wpm": 120.0,
            "word_count": 8,
            "active_speech_seconds": 4.0,
            "audio_start_seconds": 0.0,
            "audio_end_seconds": 4.0,
        },
        {"type": "stopped"},
    ]


def test_unavailable_measurement_is_null_and_unchanged_result_is_suppressed() -> None:
    short_result = provider_result(1, duration=0.5)
    browser = FakeBrowser(
        [{"type": "websocket.receive", "text": '{"type":"stop"}'}]
    )
    provider = FakeProvider(
        [short_result, short_result, {"type": "Metadata", "request_id": "safe"}]
    )

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent == [
        {
            "type": "measurement",
            "wpm": None,
            "word_count": 1,
            "active_speech_seconds": 0.5,
            "audio_start_seconds": 0.0,
            "audio_end_seconds": 0.5,
        },
        {"type": "stopped"},
    ]


def test_invalid_control_is_fatal_safe_and_cancels_provider_flow() -> None:
    browser = FakeBrowser(
        [{"type": "websocket.receive", "text": '{"type":"unknown"}'}]
    )
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
    browser = FakeBrowser(
        [{"type": "websocket.receive", "text": '{"type":"stop"}'}]
    )
    provider = FakeProvider([provider_result(8)])

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.sent[-1] == {
        "type": "error",
        "message": "Session protocol error",
    }
    assert {message["type"] for message in browser.sent} == {"measurement", "error"}
    assert provider.close_count == 1


def test_provider_drain_timeout_is_error_and_cleans_up_once() -> None:
    browser = FakeBrowser(
        [{"type": "websocket.receive", "text": '{"type":"stop"}'}]
    )
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
    provider = FakeProvider([provider_result(8)], wait_for_stop=False, hang_after_events=True)

    run(BrowserLiveWpmSession(browser, provider, drain_timeout_seconds=0.5).run())

    assert browser.cancel_count == 1
    assert provider.close_count == 1
