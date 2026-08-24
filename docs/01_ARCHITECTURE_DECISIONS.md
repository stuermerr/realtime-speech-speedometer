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

### Initial concept

- rolling window of approximately **10 seconds of active speech**;
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

Approximately ten active-speech seconds is the initial compromise, not a permanent constant.

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

**Accepted as the state-management direction; verify exact interim timestamp behavior during integration**

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

### Important validation

The spike proved frequent interim `Results` and word timestamps in final results.

During live integration, verify that interim word/timestamp payloads are consistently usable before letting the interim tail affect WPM. If not, initially calculate from finalized word history only and then revisit responsiveness.

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

**Accepted**

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

# Open Decisions

## OD-004 — Interim Result Use in WPM

Verify with real microphone input:

- whether interim `words[]` consistently contain useful timestamps;
- how often the current hypothesis changes;
- whether interim data materially improves perceived responsiveness.

## OD-006 — Yellow Pace Band

Optional.

Do not implement until the core red/green UX is working.

## OD-007 — Session Summary Grouping

Need to decide how transcript/WPM history is grouped after Stop:

- fixed time blocks;
- provider-finalized chunks;
- WPM-state-change blocks;
- another simple deterministic grouping.

Avoid semantic LLM grouping in the MVP unless clearly justified.
