# Speech Speedometer — Architecture Decision Log

This document records decisions already made, their rationale, and decisions that are intentionally still open.

Do not silently change accepted decisions during implementation.

If new evidence suggests a change, document the new decision and rationale first.

---

## ADR-001 — Backend Framework

### Status

**Accepted**

### Decision

Use **FastAPI** as the backend.

### Why

- Strong developer familiarity with Python/FastAPI.
- Natural async support for streaming workloads.
- WebSocket support.
- Keeps transcription integration, session state, and WPM logic in Python.
- Easy to navigate and explain during an interview.

### Alternatives

- Flask;
- Node/Express;
- browser-only implementation.

### Trade-offs

**Gain**

- simple Python-centric codebase;
- centralized orchestration;
- testable services.

**Lose**

- an additional backend hop compared with a direct browser → provider connection.

### Interview explanation

> I chose FastAPI because the application needs streaming communication and server-side speech-provider integration, and FastAPI gives me a small async Python backend without introducing unnecessary infrastructure.

---

## ADR-002 — Browser Does Not Hold Provider Credentials

### Status

**Accepted**

### Decision

Keep permanent speech-provider credentials in the backend.

### Recommended option

Read provider configuration from server-side environment variables.

### Why

- API keys must not be shipped to the browser.
- Centralizes provider configuration.
- Keeps the frontend relatively provider-agnostic.

### Alternatives

- direct browser connection using short-lived provider credentials where supported;
- cloud-managed identity patterns for providers/cloud services that support them.

### Trade-offs

For an interview MVP, `.env` + server-side API key is simple and defensible.

### Interview explanation

> I keep permanent provider credentials server-side. The browser only streams audio to my backend and never receives the Deepgram API key.

---

## ADR-003 — Realtime Transport Through FastAPI

### Status

**Accepted**

### Decision

Use:

```text
Browser → FastAPI → Deepgram realtime transcription
```

Use a WebSocket between browser and FastAPI.

Use a server-to-server WebSocket from FastAPI to Deepgram.

### Why

The system measures pace over multi-second speech windows.

The product is not a speech-to-speech conversational assistant where tens of milliseconds dominate the UX.

Centralizing the stream in FastAPI gives:

- one place for session state;
- one place for transcript/timestamp handling;
- one place for WPM calculation;
- simpler debugging;
- simpler interview explanation;
- no provider secret in the browser.

### Alternative

Browser → Deepgram directly with an appropriate temporary-auth pattern.

### Trade-offs

A direct media path could reduce backend bandwidth and some latency.

For this case, the backend relay is simpler and keeps the core logic in Python.

### Interview explanation

> Direct provider streaming could reduce one network hop, but the product calculates WPM over several seconds. I therefore prioritize a simple backend-mediated design with centralized state and deterministic pace logic.

---

## ADR-004 — Initial Azure `gpt-live-transcribe` Choice

### Status

**Superseded by ADR-013**

### Original decision

Start with **`gpt-live-transcribe`** through Azure / Microsoft Foundry.

### Why it was reasonable initially

- purpose-built for realtime transcription;
- available through existing Azure credits;
- direct match for realtime STT;
- avoided unnecessary general-purpose LLM/agent architecture.

### Evidence that changed the decision

The spike showed:

- frequent realtime transcript deltas;
- a final transcript;
- no usable word-level timestamps;
- no segment timestamps;
- no provider speech timeline/VAD events in the tested transcription flow.

See `docs/03_GPT_LIVE_TRANSCRIBE_FINDINGS.md`.

### Interview explanation

> Azure was my first hypothesis because it provided realtime transcription and I already had Azure available. The spike proved that the text stream was good, but the timing data required for WPM was missing, so I changed the provider based on evidence.

---

## ADR-005 — Azure Sweden Central Deployment

### Status

**Historical / no longer part of the MVP architecture**

### Original decision

Use **Sweden Central** for the Azure realtime model when required for model availability.

### Current impact

None for the accepted Deepgram MVP path.

Keep this record because it documents the Azure spike environment and the reasoning that led to the original test setup.

---

## ADR-006 — No Persistent Database for MVP

### Status

**Accepted**

### Decision

Keep session data **in memory only**.

### Data to keep temporarily

Potentially:

- finalized recognized words;
- current interim transcript/word tail;
- word timestamps;
- WPM observations;
- session start/end metadata.

### Why

The current requirements only need the data:

1. during the live session;
2. once after Stop for the summary.

There is no requirement for:

- accounts;
- history;
- analytics across sessions;
- sharing;
- long-term storage.

### Alternatives

- relational database;
- local persistent storage;
- Redis.

### Trade-offs

In-memory state disappears when the process/session ends.

That is acceptable for the current MVP.

### Interview explanation

> I did not add a database because persistence does not solve any current requirement. I would introduce persistence only if session history, accounts, or longitudinal analytics became part of the product.

---

## ADR-007 — WPM Is Deterministic Application Logic

### Status

**Accepted**

### Decision

Do not use an LLM to calculate WPM or pace status.

### Why

Given recognized words and their speech-time information:

```text
WPM = words / active_speech_minutes
```

The calculation is deterministic.

The target range is also deterministic:

```text
115–150 WPM → green
outside       → red
```

### Optional future decision

A yellow transition band may be added only if UX testing suggests it improves readability.

### Interview explanation

> Speech recognition is the AI responsibility. Pace calculation and classification are deterministic software responsibilities. This keeps the system cheaper, more reliable, and easier to test.

---

## ADR-008 — Rolling Active-Speech Metric

### Status

**Accepted, parameter still tunable**

### Decision

The live WPM represents recent **active speaking pace**, not the average over the entire session.

### Product default

- rolling window of **5 seconds of active speech**;
- **1 second of active speech** before live WPM is available;
- pauses are not counted as speaking time;
- exact pause threshold/window-edge handling remains configurable until live testing.

### Why

The user should be able to:

1. see they are speaking too fast;
2. adjust their pace;
3. receive updated feedback within a few seconds.

A full-session average would not respond quickly enough.

### Alternatives

- session average;
- fixed wall-clock window;
- fixed word-count window;
- utterance-level WPM;
- exponentially weighted smoothing.

### Trade-offs

A short window reacts quickly but can be noisy.

A long window is stable but reacts slowly.

The active-speech window is a product default rather than a permanent constant.

The shared calculation still uses complete word intervals and the one-second
pause threshold. Its live window and live availability minimum are separate,
restart-only server configuration values so microphone testing can tune the
responsiveness/stability trade-off without changing provider-independent WPM
semantics. The completed-summary availability minimum remains independently
fixed at four active-speech seconds, even when the live minimum is tuned.

### Evidence — issue #24 microphone comparison (2026-08-26)

Tested the baseline **10/4**, balanced **6/3**, responsive **4/2**, and very
responsive **2/1** profiles with steady speech, slow-to-fast and fast-to-slow
changes, a short hesitation, an approximately three-second pause, and faster
and slower resumed speech. Each run produced live measurements and a normal
provider-drained summary without browser-console errors.

The 10/4 baseline was stable but first feedback required four active seconds,
outside the approximately three-second responsiveness target. The 4/2 and 2/1
profiles felt more responsive when changing between faster and slower speech;
4/2 was initially selected because it is less reactive than 2/1. Follow-up
comparisons also covered 5/2, 5/1, and 6/2 with the same read-aloud sequence.
The operator selected 5/1 as the preferred responsiveness/stability trade-off,
accepting that it can be more volatile than longer-window profiles. Therefore
5/1 is the promoted default. The existing pause policy continued to respond
after resumed speech without explicit context-break logic, so no pause-context
feature is needed.

### Interview explanation

> The product requires current pace, so I use a local rolling measure instead of a session average. I keep the window configurable because the stability/responsiveness balance should be validated with real speech.

---

## ADR-009 — Event-Driven WPM Updates

### Status

**Accepted**

### Decision

Do **not** hard-code a separate 2-second wall-clock recalculation timer.

Preferred direction:

- update WPM when new useful Deepgram `Results` data changes the current word timeline;
- measurement-window size and update frequency remain separate;
- aim for user-visible responsiveness roughly in the 1–3 second range where the provider stream supports it.

### Why

The Deepgram spike showed frequent interim results during realtime playback.

The useful update rate naturally depends on new transcription evidence, not on an arbitrary frontend clock.

### Important rule

**Do not use network event arrival time as speech duration.**

Use Deepgram word `start` / `end` values on the audio timeline.

### Interview explanation

> I separated the 10-second measurement window from the update cadence. The UI updates when new speech evidence arrives, while the WPM denominator is based on provider audio timestamps rather than network arrival times.

---

## ADR-010 — Pause Semantics

### Status

**Accepted**

### Decision

For the MVP:

- pauses are excluded from speaking WPM;
- while paused, keep the last valid WPM visible;
- do not add a separate pause UI state initially.

### Why

This matches the metric we are defining: **speaking pace while actively speaking**.

### Primary timing evidence

Use gaps on the recognized-word audio timeline.

A configurable pause threshold will determine which gaps are excluded from active speech.

Provider VAD events may be supporting evidence but are not the primary WPM clock.

### Alternative

Include pauses in wall-clock WPM.

That measures overall delivery pace rather than active speaking pace.

### Interview explanation

> A pause should not make the system claim the presenter suddenly speaks at zero WPM. I therefore freeze the last valid pace and exclude sufficiently long word gaps from active speaking time.

---

## ADR-011 — Temporary Transcript for Summary

### Status

**Accepted**

### Decision

Keep enough transcript/timing data during the session to build a useful summary after Stop.

### Initial summary direction

Show transcript sections associated with WPM/status, for example:

```text
00:00–00:12  132 WPM  GREEN
"Good morning, today I want to ..."

00:12–00:24  158 WPM  RED
"The next important point ..."
```

### Why

- high demo value;
- makes the live metric explainable after the session;
- does not require persistent storage;
- can remain deterministic.

### Alternative

LLM-generated semantic section summaries.

### Decision on alternative

Not MVP.

Only add if the deterministic summary is complete and there is a clear product benefit.

---

## ADR-012 — Local Deterministic VAD for Azure Timing

### Status

**Superseded by ADR-013 and ADR-014**

### Original decision

Use Azure realtime transcription for text and a separate local deterministic VAD for the active-speech timeline.

### Why it was introduced

Both controlled Azure probes produced text but no usable provider speech timeline.

See:

- `docs/03_GPT_LIVE_TRANSCRIBE_FINDINGS.md`
- `docs/04_GPT_REALTIME_WHISPER_FINDINGS.md`

### Why it is no longer needed for the current MVP

The Deepgram Nova-3 spike produced word-level audio timestamps directly in the transcription stream and exposed the known long pause as a clear word-timestamp gap.

This removes the immediate need to build and align a second local VAD pipeline.

### Current position

Do **not** implement local VAD unless later real-microphone testing shows that Deepgram timestamps are insufficient for the WPM/pause definition.

### Interview explanation

> Local VAD was a fallback architecture created because Azure lacked the speech timeline I needed. Once Deepgram provided word timestamps directly, that extra component no longer justified its complexity.

---

## ADR-013 — Switch Realtime STT Provider to Deepgram Nova-3

### Status

**Accepted**

### Decision

Use **Deepgram Nova-3** as the realtime speech-to-text provider for the MVP.

### Evidence

The controlled Deepgram spike on a 54.024-second German sample produced:

- 71 `Results` events;
- 58 interim results;
- 13 final results;
- word-level start/end timestamps in final results;
- 13 `SpeechStarted` events;
- 4 `UtteranceEnd` events;
- 129 recognized final words;
- a 4.87-second gap between consecutive final words across the intentionally long pause.

See `docs/06_DEEPGRAM_NOVA_3_FINDINGS.md`.

### Why

The Speech Speedometer requires:

```text
recognized words
+
audio-timeline position of those words
```

Deepgram provides both through one realtime provider stream.

This materially simplifies the architecture compared with the Azure path.

### Alternatives

- Azure `gpt-live-transcribe` + local VAD/alignment;
- Azure `gpt-realtime-whisper` + local VAD/alignment;
- another realtime STT provider.

### Trade-offs

**Gain**

- direct word timestamps;
- easier pause handling;
- simpler WPM state;
- no immediate separate VAD subsystem;
- less custom audio/transcript alignment.

**Lose**

- Azure credits are no longer the main runtime advantage;
- adds Deepgram as an external provider dependency.

### Interview explanation

> I switched providers after the spike. Azure gave me good realtime text but not the speech timeline required for robust WPM. Deepgram returned the transcript and word timestamps in the same stream, so it removed an entire VAD/alignment component and made the system simpler.

---

## ADR-014 — Deepgram Word Timestamps Are the Primary WPM Timeline

### Status

**Accepted**

### Decision

Use Deepgram `words[].start` / `words[].end` values as the primary timing source for live WPM.

Do not use:

- server event receive timestamps;
- overall request duration;
- `SpeechStarted` count;
- `speech_final` as a proxy for active speech duration.

### Why

The spike directly demonstrated audio-timeline word timestamps and a clear gap across the known long pause.

The spike also showed that `SpeechStarted`, `is_final`, `speech_final`, and `UtteranceEnd` represent different concepts and must not be conflated.

### Consequences

- WPM logic can work on a timeline of recognized words.
- Pause exclusion can be based primarily on gaps between word intervals.
- Exact pause threshold remains tunable.
- Local VAD is not required initially.

### Interview explanation

> The provider already gives each recognized word an audio start and end time, so I use that timeline directly. This avoids letting network/model latency distort the pace calculation.

---

## ADR-015 — Finalized History Plus Replaceable Interim Tail

### Status

**Accepted**

### Decision

Represent live transcription state as:

```text
finalized word history
+
replaceable current interim tail
```

### Why

Realtime interim hypotheses may be revised.

Blindly appending every interim result would create duplicate or stale words.

Finalized results should become authoritative history.

The current interim result should be replaceable rather than permanently appended.

### Observed integration evidence

The captured Nova-3 run contained 58 interim `Results`:

- 53 had a nonempty transcript and fully timed words;
- 5 had an empty transcript and empty word list;
- growing hypotheses revised word text, word count, and timestamps;
- some later hypotheses dropped or replaced words from the prior result.

Therefore each useful `Results` payload is normalized atomically. Downstream
session state receives a complete replaceable hypothesis, never a partially
parsed or append-only interim delta.

### Interview explanation

> I separate stable finalized words from the current hypothesis. That lets the UI stay responsive without corrupting the session transcript when the speech model revises an interim result.

---

## ADR-016 — Independent Backend and Frontend Tooling

### Status

**Accepted**

### Decision

Treat `backend/` as an independent, non-packaged uv application using Python
3.13 locally and declaring support for Python 3.13 or newer.

Treat `frontend/` as a separate React/TypeScript project using Node tooling
when it is initialized.

Do not create a repository-root Python project, Node project, or uv workspace.
Run backend commands from `backend/`, or use uv's `--directory backend` option
from the repository root.

### Why

- Python and Node dependencies have different owners and lockfiles.
- Keeping each environment beside its project makes interpreter and dependency
  discovery predictable.
- A uv workspace would share a root lockfile and environment, which does not
  match these independent tooling boundaries.
- The backend is an application, so installing it as a distributable package
  would add build configuration without solving an MVP requirement.

### Consequences

- `backend/` owns `pyproject.toml`, `uv.lock`, `.python-version`, `.venv`, and
  `.env`.
- The future `frontend/` owns `package.json`, its Node lockfile, and
  `node_modules`.
- Repository-wide documentation, samples, ignore rules, agent instructions,
  and editor workspace settings remain at the root.
- Spike tools run as Python modules so application imports do not depend on
  runtime `sys.path` mutation.

### Interview explanation

> I kept Python and frontend dependencies in their owning directories. That
> gives each side one clear environment and lockfile while the repository root
> remains only the integration boundary.

---

## ADR-017 — Whole-Word Active-Speech WPM Core

### Status

**Superseded by ADR-018**

### Decision

Use a provider-independent component over a chronological timeline of validated
recognized words. Its defaults are:

- 10.0 seconds of active speech in the recent window;
- a 1.0-second pause threshold;
- 4.0 seconds of active speech before it reports WPM.

The component works from the newest word backwards and retains a complete-word
suffix until its active duration reaches or slightly exceeds the 10-second
target. It never clips a word or interval at the window edge.

Active duration is the union of overlapping word intervals plus any positive
gap shorter than the pause threshold. A gap of exactly 1.0 seconds or longer
does not count as active speech. Long pauses therefore do not reset the
measurement: older speech remains in the suffix until newer active speech
naturally moves it out.

Calculate the raw value without rounding:

```text
WPM = complete selected words × 60 / active-speech seconds
```

### Why

This produces a recent active-speech measure that is explainable, stays stable
while the speaker pauses, and uses only the provider audio timeline. Whole-word
selection avoids claiming precision the recognized transcript does not have.

The minimum active duration prevents an early value from overreacting to one or
two words, while preserving the selected count and timing information for the
caller.

### Consequences

- The core accepts only non-blank words with finite, valid audio timestamps.
- Batches are chronological and atomic; invalid input does not alter state.
- The component owns only the compact, eligible recent suffix; provider
  reconciliation, interim hypotheses, display freeze behavior, and pace-color
  classification remain outside it.
- The 10.0 / 1.0 / 4.0-second values are validated configuration defaults and
  can be tuned with later live-microphone evidence.

### Interview explanation

> I measure words over recent active speech, not wall-clock time. I retain
> complete recognized words, exclude meaningful pauses, and keep the formula
> deterministic so the result is transparent and testable.

---

## ADR-018 — Stateless Active-Speech WPM Calculation

### Status

**Accepted**

### Decision

The provider/session integration owns the complete current recognized-word
timeline. On every update it supplies that timeline to
`ActiveSpeechWpm.calculate(words)`.

The calculator retains only its validated configuration. It validates the
entire supplied timeline before calculating a result and retains no recognized
words or session state between calls. The whole-word window selection, pause
exclusion, interval union, minimum duration, and raw WPM semantics established
by ADR-017 remain unchanged.

### Why

Deepgram interim hypotheses revise text, membership, and timestamps. The
integration layer must already reconcile finalized history with the current
replaceable interim tail, so retaining a second incremental word history in the
WPM core creates competing ownership and makes corrections difficult to apply
atomically.

A pure calculation over the current provider-neutral timeline gives one owner
for transcription state, makes repeated calls deterministic, and ensures an
invalid call cannot affect a later valid measurement.

### Consequences

- Session integration owns finalized/interim word reconciliation.
- The WPM core accepts the complete chronological timeline on each call.
- Empty input produces an empty measurement, independent of previous calls.
- Invalid input raises before producing output and cannot contaminate later
  calculations.
- `RecognizedWord` and `WpmMeasurement` remain the public domain names.
- The duration field is named `active_speech_seconds` to match the metric.
- Display-freeze behavior remains a session/UI responsibility.

### Interview explanation

> Realtime transcription corrections belong to the session boundary. I pass
> its complete current word timeline into a stateless pace calculation, which
> keeps reconciliation in one place and makes WPM repeatable and isolated.

---

## ADR-019 — Browser Audio Uses Containerized WebM/Opus Passthrough

### Status

**Accepted**

### Decision

Current desktop Chrome/Chromium records microphone audio as
`audio/webm;codecs=opus` and sends each non-empty `MediaRecorder` Blob as one
ordered binary browser WebSocket message. FastAPI forwards those bytes directly
to one Deepgram Nova-3 `/v1/listen` connection without transcoding.

The browser transport uses a distinct containerized-audio mode. Its Deepgram
query omits `encoding`, `sample_rate`, and `channels`, allowing the provider to
read framing from the WebM container. The historical fixed-sample spike keeps
its explicit raw `linear16`, 24 kHz, mono mode unchanged.

`MediaRecorder.start(100)` requests approximately 100 ms chunks. This value is
a transport cadence, not a speech clock; WPM continues to use provider word
timestamps.

### Evidence

- Deepgram documents that `encoding` is required for raw headerless packets and
  must not be used for containerized audio.
- Deepgram's v1 streaming API accepts binary media and a JSON `CloseStream`
  command.
- Current Chrome/Chromium exposes WebM/Opus through `MediaRecorder`; support is
  checked before microphone access.
- A real microphone comparison of the same Dual 2s/8s, 30/70 profile found
  100 ms visibly more responsive than 250 ms. The secret-safe diagnostic run
  forwarded 832 audio chunks and received 141 Deepgram `Results`, producing
  113 timeline-changing measurements in approximately 100 seconds, without
  browser-console errors.

See:

- https://developers.deepgram.com/docs/encoding
- https://developers.deepgram.com/reference/speech-to-text/listen-streaming
- https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static

### Consequences

- No browser or backend resampling, decoding, or transcoding is introduced.
- Audio continues through speech and silence until explicit Stop.
- Direct passthrough must be validated with a real microphone and Deepgram.
- If it fails, diagnose and record the failure before considering PCM,
  AudioWorklet, resampling, or transcoding in a new decision.

### Interview explanation

> Chrome already produces a supported streaming container. Passing it through
> unchanged removes an unnecessary media pipeline while Deepgram remains the
> owner of speech timestamps.

---

## ADR-020 — Vanilla Browser Adapter for Local Tracer-Bullet Validation

### Status

**Accepted, temporary adapter**

### Decision

Serve a small HTML/CSS/JavaScript debug client from FastAPI at
`http://localhost:8000/`. Do not initialize the planned React/TypeScript
frontend during the browser-to-WPM tracer bullet.

The adapter owns only browser capabilities and resources:

- capability checks and microphone permission;
- `MediaRecorder` lifecycle and ordered binary chunk sending;
- Start/Stop controls and debug session states;
- rendering backend measurements, stopped notifications, and safe errors.

FastAPI continues to own the Deepgram connection, credentials, transcription
orchestration, word state, and deterministic WPM pipeline.

### Why

The current milestone tests the media and session-lifecycle architecture, not
the polished product interface. A framework would add build tooling and UI
structure without improving that evidence.

### Consequences

- There is no repository-root or frontend Node project for this milestone.
- The adapter is not the final large-display pace-color or summary UI.
- A later product-UI issue may replace it without changing the WebSocket
  session protocol or WebM/Opus transport.

### Interview explanation

> I isolated the risky end-to-end media path with the smallest browser adapter.
> That proves the durable transport and backend lifecycle before investing in
> the polished interface.

---

## ADR-021 — React Product UI and Backend-Authoritative Live Pace Protocol

### Status

**Accepted**

### Decision

Replace the temporary Vanilla debug client with one strict TypeScript React UI
built by Vite. During development, Vite proxies `/ws/live` to FastAPI. For the
production artifact, FastAPI serves the built frontend and retains `/health`
and `/ws/live` as backend-owned routes. There is one browser product UI, not a
parallel debug client.

Each backend `measurement` message carries `wpm` and `pace_status` with this
invariant:

```text
wpm available     <=> pace_status is "green" or "red"
wpm unavailable   <=> pace_status is null
```

The backend classifies raw WPM before display rounding. The inclusive target
range is 115–150 WPM; every other available value is red. There is no yellow
state. The frontend treats an invalid WPM/status combination as a protocol
error rather than reclassifying it.

The browser owns a small session adapter for capability checks, microphone and
`MediaRecorder` lifecycle, ordered audio delivery, the WebSocket connection,
a 10-second connection timeout, Stop, and idempotent cleanup. A local reducer
owns only visible lifecycle state: `idle`, `starting`, `listening`,
`finalizing`, `completed`, `error`, and `unsupported`.

The live presentation uses a fixed linear 60–220 WPM scale. Numeric WPM is
never clamped; only marker position is clamped for presentation. The UI retains
the last available WPM, status, and marker when a later measurement is
unavailable, and clears them when a fresh session starts.

### Why

- Pace classification is product logic and must be deterministic and identical
  for every client.
- Raw-value classification avoids displaying 150 for a value such as 150.4
  while incorrectly showing it as in range.
- One browser adapter localizes resource ownership and makes failure cleanup
  testable without introducing global state infrastructure.
- A fixed scale makes direction and target boundaries stable at presentation
  distance while preserving the actual measured value.
- React and strict TypeScript provide a small, explicit product-state model for
  the final browser experience; Vite keeps development and production tooling
  isolated in `frontend/` as required by ADR-016.

### Consequences

- Backend and frontend tests both exercise the public WebSocket message shape.
- Unsupported environments stop before microphone permission and do not offer
  a meaningless retry.
- Recoverable errors offer a retry that creates an entirely new adapter and
  session.
- Stop transitions the UI immediately to `finalizing`; the adapter preserves
  the recorder's final chunk, waits for ordered sends, sends the existing Stop
  command, and waits for the backend's provider-drained `stopped` message.
- This M5 slice does not change ADR-011: the accepted deterministic session
  summary remains required, but its implementation is owned by issue #17.

### Interview explanation

> The backend owns the raw pace classification, while the browser owns only
> presentation state and media resources. A small session adapter and reducer
> make the lifecycle explicit, and one fixed scale keeps feedback legible and
> predictable across the presentation.

---

## ADR-022 — Deterministic Global Session Summary and Inactivity Finalization

### Status

**Accepted**

### Decision

After a successful Deepgram provider drain, the backend emits exactly one
authoritative `summary` event followed by exactly one `stopped` event. The
summary is derived only from the immutable, session-owned finalized-word
timeline; interim hypotheses, provider chunk text, and provider chunk
boundaries are not retained for it.

The global summary contains four unrounded quantitative values:

- `average_speaking_pace` (or `null` when active speech is below four seconds);
- `finalized_words`;
- `active_speaking_seconds`;
- `presentation_duration_seconds`.

Active speech uses the same interval and gap semantics as live WPM: word
intervals and gaps below one second count; gaps of one second or more do not.
Its availability minimum remains fixed at four active-speech seconds,
independently of the configurable live WPM minimum. Presentation duration is
the first finalized word start through the last finalized word end. It is not
wall-clock session time and the summary pace is never an average of rolling
live measurements. An empty finalized timeline produces a valid empty summary.

The browser stores a received summary as pending and reveals it only after the
following `stopped` event. An error or disconnect discards pending data. It
rounds Average WPM for display and formats durations as `m:ss`, without
changing backend values. A fresh presentation clears all live, pending,
completed, reason, and error state.

The backend tracks recognized-speech progress by the strictly advancing maximum
recognized word end timestamp. Five minutes without progress requests a normal
browser Stop with reason `inactivity`; the browser follows its existing final
audio/Stop path. The backend waits at most five seconds for that acknowledgement
before best-effort cleanup, while retaining the separate five-second provider
drain timeout. Inactivity after speech therefore produces the normal summary
plus a neutral explanation; inactivity without speech produces the honest empty
completion plus that explanation.

### Why

- Finalized provider timing is the only authoritative evidence available after
  a session ends.
- A small global summary is useful at presentation distance without inventing
  semantic or provider-chunk groupings.
- Ordering summary before `stopped` prevents the UI from presenting partial
  completion data.
- Timestamp progress avoids treating repeated unchanged hypotheses as speech.

### Consequences

- The initial MVP resolves OD-007 with **no transcript segments**. Segment
  analysis remains post-MVP and must be added as a complete vertical feature,
  not as retained unused chunk data.
- Successful finalization has one protocol order: `summary`, then `stopped`.
  Drain failures, protocol failures, and disconnects never emit a successful
  summary.
- The browser adapter accepts the server's `stop_requested` control message
  and owns microphone/recorder shutdown just as it does for a user Stop.

### Interview explanation

> I calculate the final summary from one finalized speech timeline, not from
> UI samples. That keeps every metric deterministic, makes pauses explicit, and
> lets normal Stop and inactivity share the same reliable drain path.

---

## ADR-023 — Final-Chunk Pace Segments and Dedicated Completed Layout

### Status

**Accepted**

### Decision

Extend ADR-022's provider-drained `summary` payload with chronological
`segments`. During a session, retain immutable chunks only for non-empty
Deepgram `Results` events whose `is_final` value is true. Each chunk preserves
the provider's formatted transcript text and normalized timed words;
`speech_final`, endpointing events, and semantic sentence processing do not
define segment boundaries.

The summary calculator continues to flatten all finalized chunks for the four
global metrics. Separately, it greedily groups whole adjacent chunks until the
group reaches at least four active-speech seconds. It never splits a chunk and
merges a final short remainder into the preceding group. If the whole
presentation is shorter, it emits one pace-unavailable segment. Segment text is
trimmed and joined with one space; punctuation and internal formatting remain
provider-authored.

Each segment exposes only `text`, unrounded `average_speaking_pace`, and
backend-classified `pace_status` (`green`, `red`, or `null`). Segment WPM uses
the same one-second active-speech gap policy as global and live WPM, but is
calculated independently. Consequently, segment active durations need not sum
to the global active duration: a sub-threshold gap crossing two emitted segment
boundaries counts globally but is absent from both independent segment
timelines.

After ordered `summary` then `stopped`, React switches from the viewport-sized
Live layout to a vertically scrollable Completed layout. It shows global
metrics followed by every transcript segment and a compact variant of the
shared value-driven pace scale. Each desktop segment is vertically divided
into transcript text on the left and pace analysis on the right; narrow screens
stack those regions. An unavailable segment uses a visibly neutral marker that
does not imply a pace classification. React rounds WPM for display, presents backend
`green` as `On pace`, and derives `Too slow` or `Too fast` only when the backend
sent `red`. The Completed layout omits the live reading and full-size scale.

### Why

- Final provider chunks are the smallest authoritative formatted transcript
  units already available without semantic inference.
- Whole-chunk grouping gives useful local pace feedback while keeping the
  algorithm deterministic and explainable.
- A dedicated Completed hierarchy removes a misleading frozen live reading and
  makes an uncapped transcript recap usable on long presentations.

### Consequences

- Session memory now retains finalized chunk text as well as timed words, but
  nothing persists beyond the active browser session.
- Empty final Results still clear the interim tail but create no segment.
- Parser failure, provider failure, or drain failure discards pending summary
  data and never enters Completed.
- This post-MVP decision supersedes only ADR-022's explicit no-segments
  consequence; ADR-022's global metrics and finalization protocol remain
  accepted.

### Interview explanation

> I keep Deepgram's final chunks as trustworthy formatting boundaries, combine
> them deterministically into useful pace windows, and show them only after the
> same provider-drained completion handshake as the global summary.

---

# Open Decisions

## OD-006 — Yellow Pace Band

**Closed by ADR-021.** The MVP uses only the explicit inclusive green range and
red outside it. A yellow band is not justified by current evidence.

## OD-007 — Session Summary Grouping

**Closed by ADR-023.** The MVP historically shipped the ADR-022 global summary;
the post-MVP enhancement now groups whole finalized provider chunks into
deterministic transcript pace segments as one complete vertical feature.
