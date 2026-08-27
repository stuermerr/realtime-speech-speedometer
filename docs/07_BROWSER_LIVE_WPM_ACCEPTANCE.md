# Browser Live WPM Manual Acceptance

This procedure validates the real boundary that provider fakes cannot cover:

```text
Chrome microphone → WebM/Opus → FastAPI → Deepgram Nova-3 → live WPM
```

Do not close issue #12 until every required result below is recorded as passed.
Never paste credentials, full provider payloads, or sensitive transcript text
into this document.

## Preconditions

- Current desktop Chrome or Chromium is installed.
- A working microphone is available.
- `backend/.env` contains a valid `DEEPGRAM_API_KEY`.
- The backend automated suite and strict type check pass.

Start the local app from the repository root:

```bash
uv run --directory backend uvicorn app.main:app --reload
```

Open http://localhost:8000/ in a fresh browser tab.

## Secret-safe diagnostic capture

Additional live-session diagnostics are disabled by default. For a local
diagnosis, start the backend with the one opt-in setting and capture both
standard output and standard error:

```bash
LIVE_WPM_DEBUG=true uv run --directory backend uvicorn app.main:app 2>&1 \
  | tee /tmp/speech-speedometer-live-wpm-debug.log
```

Each diagnostic message is JSON with a session-local `session_id`, monotonic
`relative_seconds`, `stage`, and `event`. Records include byte sizes, safe
Deepgram event metadata, WPM processing decisions, Stop/drain outcomes, and
cleanup. They intentionally omit audio, transcript text, raw provider payloads,
credentials, headers, device identifiers, and exception details.

Stop the server after reproducing the problem, inspect or share only the
needed capture, then remove the temporary file:

```bash
rm -f /tmp/speech-speedometer-live-wpm-debug.log
```

Omit `LIVE_WPM_DEBUG=true` for normal sessions. If the setting is placed in
`backend/.env` instead, set it back to `false` after diagnosis.

## Procedure

1. Record the browser version, operating system, and selected microphone below.
2. Press Start and grant microphone permission.
3. Confirm the state sequence is requesting microphone → connecting → listening.
4. In browser developer tools, record the `MediaRecorder.mimeType` shown by the
   listening state and representative non-empty WebSocket chunk sizes. Do not
   record audio contents.
5. Speak German continuously for at least 15 seconds. Record approximate time
   from speech to the first useful WPM value and subsequent update behavior.
6. Pause silently for at least 5 seconds. Confirm the connection remains open,
   audio continues, and the displayed WPM does not fall to zero.
7. Resume speaking. Confirm WPM updates resume without reconnecting.
8. Speak a distinctive final phrase and immediately press Stop. Confirm the UI
   remains in stopping until the final phrase has been processed, Metadata was
   observed, Deepgram closed normally, and the backend sends `stopped`.
9. Press Start again and complete a second short session. Confirm no transcript
   or WPM state carries over.
10. Deny microphone permission in a fresh permission state and confirm an
    understandable error plus released media resources.
11. Stop the backend during an active session and confirm an understandable
    connection error plus released recorder and microphone resources.
12. Where practical, temporarily force the WebM/Opus capability check to fail
    in developer tools and confirm microphone permission is not requested.

## Recorded result

Status: **PASS — transport, live speech, pause/resume, final-word drain,
second-session isolation, and error paths passed.**

| Evidence | Recorded value |
| --- | --- |
| Test run | Recorded manual acceptance run |
| Chrome/Chromium version | Current stable Google Chrome using a headed browser session |
| Platform | Linux x86_64 |
| Microphone | Default physical input; device-specific identifier omitted |
| Actual `MediaRecorder.mimeType` | `audio/webm;codecs=opus` — pass |
| Representative chunk sizes | 965–4,862 bytes; 4,846-byte steady chunks — pass |
| First useful WPM latency | Pass — 6.77 seconds after session start, approximately 6.18 seconds after the first `SpeechStarted` event |
| Continued updates while speaking | Pass — 43 changed-timeline measurements were sent during the first 47.53-second session |
| Pause retained last WPM | Pass — a 6.88-second word-timeline gap (16.84–23.72 seconds) produced only unchanged empty Results; measurements were suppressed and the displayed value remained unchanged |
| Resume updated without reconnect | Pass — the same provider/browser session sent a new measurement at 24.77 seconds |
| Final phrase preserved after Stop | Pass — the second session received a four-word timed Result and sent its changed measurement after Stop, before Metadata and `stopped` |
| Metadata observed | Pass — session one at 47.522 seconds and session two at 20.329 seconds |
| Provider normal close observed | Pass — both sessions logged `stream_closed` with `outcome=normal`; no failures, timeouts, or abnormal closes occurred |
| Clean stopped notification ordering | Pass — Stop → final Results/measurement → Metadata → normal close → `stopped`; the final browser state was `WPM: 125` and `Stopped cleanly after final transcription.` |
| Permission-denial behavior | Pass — clear error: `Microphone permission was denied. Allow access and try again.` |
| Unsupported-format behavior | Pass — WebM/Opus error with zero `getUserMedia` calls |
| Backend/provider-failure behavior | Pass — backend shutdown produced an understandable error; recorder became inactive and all tracks were released. The run exposed and led to a fix for a final-close `WebSocketDisconnect` traceback. |
| Second-session fresh state | Pass — distinct session IDs, initially unavailable WPM, and no carried-over state; browser console reported zero errors and warnings |

The headed Chrome run transported real human speech through two complete
Deepgram sessions. Secret-safe diagnostics proved the active-speech pause
semantics, post-Stop final-word processing, normal close ordering, and fresh
session state without recording audio or transcript text.

## Failure handling

If direct WebM/Opus passthrough fails, record the Chrome version, actual MIME
type, representative chunk sizes, safe provider error category, and the point
of failure. Do not add AudioWorklet capture, PCM conversion, resampling, or
transcoding without a separate architecture decision based on that evidence.
