# Speech Speedometer — Project Brief

## 1. Purpose

Build a web application for rhetoric training that gives a presenter live feedback about speaking speed.

The central user problem:

- Nervous presenters often speak too fast.
- During a presentation, they need feedback that can be understood at a glance.
- The application should behave like a speedometer for speech.
- The user actively starts and stops a measurement session.
- During the session, the application shows the current speaking pace in words per minute (WPM).
- After the session, it shows a short summary.

The goal is **not** to build the most sophisticated speech system possible.

The goal is:

> Build the simplest technically sound solution that solves the case convincingly, can be implemented quickly, and can be clearly defended in a technical interview.

---

## 2. Original Case Study Requirements

### Explicit functional requirements

- The user actively starts the measurement through the UI.
- The user actively stops the measurement through the UI.
- While the user is speaking, the current WPM value is continuously updated.
- The WPM value must be large and readable from several meters away.
- Target speaking range: **115–150 WPM**.
- Traffic-light status:
  - **green** inside the target range;
  - **red** outside the target range.
- A yellow transition range is optional and must be justified if introduced.
- The displayed WPM must represent the **current pace**, not the average over the entire session.
- The exact method for calculating current pace is a design decision.
- After stopping, the user sees a short session summary.
- Expected errors must be handled cleanly, including:
  - missing microphone permission;
  - unsupported environment;
  - longer speaking pauses.

---

## 3. Primary User Workflow

```text
Open application
    ↓
Start session
    ↓
Grant / use microphone
    ↓
Stream speech audio
    ↓
Deepgram Nova-3 realtime transcription
    ↓
Receive transcript results + word timestamps
    ↓
Calculate current speaking WPM deterministically
    ↓
Display large WPM value + pace status
    ↓
Continue until user presses Stop
    ↓
Build session summary from temporary session data
    ↓
Display summary
```

---

## 4. Product Decisions Made So Far

### 4.1 Current WPM, not session-average WPM

The live value should represent recent speaking pace.

Reason:

- A presenter may see that they are speaking too fast.
- They then deliberately slow down.
- The displayed value should react within a few seconds.
- A session-wide average would react too slowly and provide misleading feedback.

### 4.2 Rolling active-speech window

Working concept:

- approximately **10 seconds of active speech** form the rolling measurement window;
- pauses should not count as speaking time;
- the exact threshold and edge behavior remain configurable until tested with live microphone sessions.

Important:

- `10 seconds` is an initial product parameter, not a fixed external requirement.
- Do **not** hard-code a separate 2-second wall-clock hop.
- Prefer **event-driven WPM recalculation** when new useful Deepgram `Results` data arrives.
- Measurement-window size and UI update frequency are separate concerns.
- Desired user-facing responsiveness is roughly in the **1–3 second range** where provider output supports it.

### 4.3 Pause behavior

For the MVP:

- pauses do not reduce the speaking WPM;
- during a pause, keep showing the **last valid WPM value**;
- do not add a special `PAUSE` UI state initially;
- optional pause visualization can be added later if it improves the demo.

Conceptually:

```text
Speaking → 138 WPM
Pause    → still show 138 WPM
Speaking → new recognized words arrive → calculate updated WPM
```

This means the metric represents **speaking pace during active speech**, not overall delivery pace including silence.

Deepgram provides word-level `start` and `end` timestamps on the audio timeline. The MVP should use those timestamps as the primary timing evidence for WPM and pause handling instead of provider-event receive timestamps.

### 4.4 Temporary transcript/session data

Keep transcript and timing-related information temporarily for the active session.

Useful temporary state may include:

- finalized recognized words;
- the current replaceable interim transcript/word tail;
- word-level start/end timestamps;
- WPM observations;
- session start/end metadata.

Purpose:

- live WPM calculation;
- final session summary;
- optionally map transcript sections to their pace/WPM.

Do **not** persist sessions beyond the current app session for the MVP.

No database is currently required.

### 4.5 Session summary direction

A useful summary contains four global deterministic metrics:

- unrounded average speaking pace when at least four active-speech seconds
  exist;
- finalized word count;
- active speaking duration;
- presentation duration from first finalized word start to last finalized word
  end.

The backend sends `summary` followed by `stopped` only after a successful
provider drain. The browser rounds WPM and formats duration values for display,
but stores the summary pending until the following `stopped` event. Empty
presentations show `No speech was detected`; short presentations show their
word/time metrics and honestly mark Average WPM unavailable.

Summary is derived from finalized words only, never from averaging live rolling
measurements. The completed post-MVP experience also shows the full transcript
as chronological pace segments. These segments preserve non-empty Deepgram
final-chunk boundaries, combine adjacent chunks until at least four active
speech seconds when possible, and never split a provider chunk. Each available
segment carries its own deterministic unrounded Average WPM and backend
red/green classification. A short presentation produces one segment with pace
unavailable; an empty presentation produces no segments.

After successful finalization, Completed is a content-oriented, vertically
scrollable view containing the global metrics, transcript pace recap, optional
inactivity notice, and `Start new presentation`. The dominant live reading and
full-size live scale are not shown after completion. Failed finalization remains
an Error and must never resemble Completed. This is a post-MVP enhancement; the
original MVP scope and its historical M6 acceptance record are unchanged.

Do not add an LLM-based semantic summary unless there is a clear benefit and time remains.

---

## 5. Current Technical Constraints / Preferences

### Backend

Use **Python + FastAPI**.

Reason:

- developer familiarity;
- good async/WebSocket support;
- keeps orchestration, provider integration, session state, and WPM logic in Python.

### Realtime speech-to-text provider

Use **Deepgram Nova-3** for the MVP.

This decision was made after empirical spikes:

- Azure `gpt-live-transcribe` provided frequent realtime text deltas but no usable word/segment speech timeline in the tested flow.
- Azure `gpt-realtime-whisper` showed the same limitation.
- Deepgram Nova-3 returned:
  - realtime/interim `Results`;
  - finalized `Results`;
  - word-level `start` / `end` timestamps;
  - `SpeechStarted` events;
  - `UtteranceEnd` events.

The known long pause in the controlled sample was directly visible as a large gap between consecutive word timestamps.

See:

- `docs/03_GPT_LIVE_TRANSCRIBE_FINDINGS.md`
- `docs/04_GPT_REALTIME_WHISPER_FINDINGS.md`
- `docs/06_DEEPGRAM_NOVA_3_FINDINGS.md`

### Why Deepgram fits this product better

The Speech Speedometer needs two things:

```text
recognized words
+
when those words occurred on the audio timeline
```

Deepgram exposes both in one realtime stream.

This removes the current need for:

- a separate local VAD component;
- custom alignment between an Azure transcript stream and a local speech timeline;
- artificial periodic Azure commit boundaries solely to create timing segments.

### Secrets

Provider credentials stay server-side.

For the interview MVP:

- configuration through environment variables / local `.env`;
- `.env` must be ignored by Git;
- never expose the Deepgram API key to the browser.

Conceptual variable:

```text
DEEPGRAM_API_KEY=...
```

---

## 6. Complete Realtime Data Flow

This is the complete live feedback loop from speech to the displayed WPM value:

```text
YOUR VOICE
  ↓
Microphone hardware
  ↓
Browser navigator.mediaDevices.getUserMedia()
  ↓
MediaStream
  ↓
MediaRecorder
  ↓
WebM/Opus Blob approximately every 250 ms
  ↓
Browser WebSocket
  ↓
Binary WebSocket frame
  ↓
FastAPI WebSocket
  ↓
Python receives bytes
  ↓
Deepgram WebSocket
  ↓
Same audio bytes (no transcoding)
  ↓
Deepgram Nova-3
  ↓
Results event
  ↓
Words + audio-timeline timestamps
  ↓
ParsedDeepgramResult
  ↓
SessionWordState
  ↓
ActiveSpeechWpm.calculate(...)
  ↓
WpmMeasurement
  ↓
FastAPI serializes measurement JSON
  ↓
Browser WebSocket
  ↓
Text frame
  ↓
JavaScript receives and parses the message
  ↓
JavaScript updates the HTML
  ↓
USER SEES "WPM: 132"
```

The important boundary details are:

- `MediaRecorder.start(250)` requests the approximately 250 ms transport
  cadence; it is not the WPM measurement clock.
- FastAPI passes the containerized `audio/webm;codecs=opus` bytes to Deepgram
  without decoding, resampling, or transcoding them.
- Deepgram word timestamps provide the speech timeline. Network receive time is
  never used to calculate WPM.
- `SessionWordState` combines finalized words with the current replaceable
  interim tail before `ActiveSpeechWpm` calculates the latest measurement.
- FastAPI sends a JSON `measurement` message back to the browser only when the
  visible word timeline changes. During pauses, the UI therefore retains the
  last valid WPM value.

This sequence is the complete realtime loop. Start/stop coordination and the
final session summary wrap around it but do not change the live measurement
path.

The frontend should remain simple:

- capture microphone audio;
- stream it;
- display backend state;
- start/stop the session;
- render the final summary.
- render every completed transcript pace segment with a compact shared scale.

---

## 7. Important Non-Goals

Do **not** introduce these unless a concrete requirement emerges:

- AI agents;
- LangGraph;
- RAG;
- embeddings;
- vector database;
- relational database;
- Redis;
- Kafka;
- microservices;
- Kubernetes;
- user accounts;
- authentication;
- persistent session history;
- long-term transcript storage;
- complex cloud infrastructure;
- a separate local neural VAD unless Deepgram timing proves insufficient in real microphone tests.

Do not use an LLM to calculate WPM.

WPM and traffic-light status must be deterministic application logic.

---

## 8. Evidence From Technical Spikes

### Azure `gpt-live-transcribe`

Observed:

- frequent incremental transcript deltas;
- first useful partial transcript around 2 seconds;
- no word timestamps;
- no segment timestamps;
- no provider speech-start/stop timeline in the tested transcription flow.

Conclusion:

- usable for realtime transcription;
- not a good fit for this WPM architecture without additional timing logic.

### Azure `gpt-realtime-whisper`

Observed:

- realtime transcript deltas;
- final transcript;
- no usable word/segment timestamps;
- no VAD speech timeline.

Conclusion:

- did not solve the timing limitation.

### Deepgram Nova-3

Controlled 54.024-second sample:

- 71 `Results` events;
- 58 interim results;
- 13 final results;
- word-level start/end timestamps in final results;
- 13 `SpeechStarted` events;
- 4 `UtteranceEnd` events;
- known long pause appeared as a 4.87-second gap between consecutive final words.

Conclusion:

> Deepgram currently provides the best fit for the MVP because the transcript and audio-timeline evidence required by WPM arrive from the same provider stream.

---

## 9. Open Questions

### WPM algorithm

The provider decision is now sufficiently clear. The next algorithmic questions are:

- exact definition of the approximately 10-second **active-speech** rolling window;
- how much silence between words counts as a pause to exclude;
- minimum amount of speech required before showing the first WPM value;
- behavior when the rolling window only partially overlaps an old word interval;
- whether any additional smoothing is required after real microphone testing.

### Interim versus finalized Deepgram results

Deepgram produces interim and finalized `Results`.

The implementation must avoid double-counting.

Preferred state model:

```text
finalized word history
+
replaceable current interim tail
```

Before relying on interim word timestamps for live WPM, verify their consistency in the real microphone integration. Finalized results remain authoritative.

### Pause signals

Do not equate these concepts:

- `is_final`;
- `speech_final`;
- `SpeechStarted`;
- `UtteranceEnd`.

The spike showed they are not synonyms.

For WPM, word timestamps should be the primary timeline. Provider VAD/utterance events may be useful supporting signals.

### Frontend technology

Not yet selected.

The frontend must:

- capture microphone audio;
- start/stop a session;
- stream audio to FastAPI;
- display a very large WPM value;
- display pace status;
- show the final summary.

Prefer the simplest UI stack that gives a convincing demo.

### Yellow pace band

Optional.

Start with the explicit requirement:

```text
115–150 WPM → green
outside       → red
```

Only add yellow if it clearly improves the UX.

---

## 10. Success Criteria for the MVP

The MVP is successful if:

- the user can start and stop a session;
- microphone errors are handled clearly;
- live audio reaches Deepgram through the backend;
- realtime speech results are received;
- word timestamps can drive a recent active-speech WPM metric;
- current WPM reacts to recent speech rather than the full-session average;
- pauses do not incorrectly drive speaking WPM toward zero;
- the live value is readable from several meters away;
- 115–150 WPM is clearly shown as the target range;
- the session summary is useful and understandable;
- the implementation is small enough to explain confidently in an interview.

---

## 11. Engineering Principle

When choosing between two valid designs:

> Prefer the simpler solution unless the more complex solution clearly improves the user experience, reliability, or interview value.

The provider switch from Azure/OpenAI to Deepgram is an example of this principle: empirical evidence showed that Deepgram exposes the timing data the product needs directly, eliminating additional components.
