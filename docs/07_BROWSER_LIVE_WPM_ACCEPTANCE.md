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

Status: **PARTIAL — transport and error paths passed; speech scenarios require a
human utterance through the physical microphone.**

| Evidence | Recorded value |
| --- | --- |
| Date/time and timezone | 2026-08-24 19:12–19:14 CEST |
| Chrome/Chromium version | Google Chrome 149.0.7827.200 (Playwright headless Chrome channel) |
| Platform | Linux 6.17.0-35-generic x86_64 |
| Microphone | Default PipeWire/PulseAudio source `alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp_6__source` |
| Actual `MediaRecorder.mimeType` | `audio/webm;codecs=opus` — pass |
| Representative chunk sizes | 2,442-byte initial chunk; 4,845–4,846-byte steady/final chunks — pass |
| First useful WPM latency | Not exercised; no human speech was produced |
| Continued updates while speaking | Not exercised; no human speech was produced |
| Pause retained last WPM | Not exercised; no human speech was produced |
| Resume updated without reconnect | Not exercised; no human speech was produced |
| Final phrase preserved after Stop | Not exercised; no human speech was produced |
| Metadata observed | Pass — both sessions reached `stopped`, which the backend gates on Metadata |
| Provider normal close observed | Pass — both sessions reached the normal-close-gated `stopped` path |
| Clean stopped notification ordering | Pass — UI remained stopping, then reported clean stopped |
| Permission-denial behavior | Pass — clear error: `Microphone permission was denied. Allow access and try again.` |
| Unsupported-format behavior | Pass — WebM/Opus error with zero `getUserMedia` calls |
| Backend/provider-failure behavior | Pass — backend shutdown produced an understandable error; recorder became inactive and all tracks were released. The run exposed and led to a fix for a final-close `WebSocketDisconnect` traceback. |
| Second-session fresh state | Pass for clean lifecycle with a new WebSocket/provider connection; automated test also proves fresh WPM state |

The real microphone transported silence successfully through two complete
Deepgram sessions. The remaining speech, pause/resume, latency, and final-word
checks must be completed by a human speaking into the microphone before issue
#12 is closed.

## Failure handling

If direct WebM/Opus passthrough fails, record the Chrome version, actual MIME
type, representative chunk sizes, safe provider error category, and the point
of failure. Do not add AudioWorklet capture, PCM conversion, resampling, or
transcoding without a separate architecture decision based on that evidence.
