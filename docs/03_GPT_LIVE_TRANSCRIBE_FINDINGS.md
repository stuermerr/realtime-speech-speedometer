# `gpt-live-transcribe` Spike Findings

## Current Decision Status

**Completed evidence; provider decision later superseded.**

These findings remain valid for the tested Azure deployment.

The original implication — combine Azure text with a separate local VAD timeline — was later superseded after the Deepgram Nova-3 spike demonstrated word-level audio timestamps in the realtime transcription stream.

Current MVP provider: **Deepgram Nova-3**.

See `docs/06_DEEPGRAM_NOVA_3_FINDINGS.md` and ADR-013/ADR-014 in `docs/01_ARCHITECTURE_DECISIONS.md`.


## Environment

- Azure region: not recorded in the local run report
- model/deployment: configured `gpt-live-transcribe` deployment
- API version: GA Realtime API (`/openai/v1/realtime`)
- transport: server-to-server WebSocket
- audio format: target PCM16 mono at 24 kHz

## Event Types Observed

| Event type | Purpose | Important fields |
| --- | --- | --- |
| `session.created` | Confirms the realtime connection and default session | `event_id`, `session` |
| `session.updated` | Confirms the accepted transcription configuration | `event_id`, `session` |
| `conversation.item.input_audio_transcription.delta` | Incremental transcript text; observed 177 times | `event_id`, `item_id`, `content_index`, `delta` |
| `input_audio_buffer.committed` | Confirms the final client buffer commit | `event_id`, `item_id`, `previous_item_id` |
| `conversation.item.added` | Adds the committed input-audio item | `event_id`, `item`, `previous_item_id` |
| `conversation.item.done` | Marks the input-audio item done | `event_id`, `item`, `previous_item_id` |
| `conversation.item.input_audio_transcription.completed` | Supplies the final transcript and usage | `event_id`, `item_id`, `content_index`, `transcript`, `usage` |
| `error` | Rejected the initial unsupported VAD configuration | `error.type`, `error.code`, `error.param`, `error.message` |

## Partial / Incremental Transcript

- Event type: `conversation.item.input_audio_transcription.delta`
- Example shape: `event_id`, `item_id`, `content_index: 0`, and a short `delta` string
- Append-only or revision: the 177 deltas concatenated to the final transcript except for one leading space removed by the completed transcript; no interior revision was observed in this sample
- Typical observed frequency: median receive gap about 195 ms; largest observed gap about 1.617 seconds

The first delta arrived about 1.950 seconds after paced sending began. Deltas
continued during playback, before the final client commit.

## Completed Transcript

- Event type: `conversation.item.input_audio_transcription.completed`
- Example shape: `event_id`, `item_id`, `content_index: 0`, `transcript`, and `usage`
- Relationship to partial transcript: the 783-character final German transcript matched concatenated deltas after trimming one leading space

## VAD / Speech Timing

- speech-start event: not observed
- speech-stop event: not observed
- timing fields: not observed
- unit: not observed

The first real connection returned `invalid_request_error` with code
`invalid_value` for `session.audio.input.turn_detection`: “Turn detection is
not supported for this transcription model.” The provider rejected the
session before audio streaming began.

### Evidence-driven probe adjustment

The initial spike plan requested server VAD and no client commits. The deployed
model cannot accept that configuration, so the next probe will set turn
detection to `null` and send one `input_audio_buffer.commit` after the complete
paced sample. This is a single end-of-sample boundary, not periodic commits.
It was the smallest change that could continue the empirical spike without
switching models or inventing timing data. The adjusted run was accepted and
completed.

## Timestamp Granularity

- word-level timestamps: not observed
- segment-level timestamps: not observed
- other useful timing: local monotonic receive time is captured for every raw event; the completed event reported 51 seconds of audio usage, but no speech/VAD interval

### Targeted segment-event follow-up

The [Azure GA realtime event reference](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/realtime-audio-reference-ga)
documents
`conversation.item.input_audio_transcription.segment` with segment `id`,
`text`, `speaker`, `start`, and `end` fields. It does not establish that the
configured `gpt-live-transcribe` deployment emits this event. The compact probe
report now retains those fields, plus `usage` and `logprobs`, when Azure sends
them; the raw JSONL was already complete.

The follow-up run used `sample_02.wav`, a 54.024-second sample with a measured
4.554-second silence from about 8.419 to 12.973 seconds (`silencedetect` at
-40 dB with a 2-second minimum). The run completed with
178 compact provider events: 174 transcript deltas, one buffer commit, one item
added event, one item done event, and one completed-transcription event. The
complete raw capture additionally contained `session.created` and
`session.updated`.

Observed result:

- `conversation.item.input_audio_transcription.segment`: 0 events
- `start` / `end`: 0 occurrences in the raw provider events
- `audio_start_ms` / `audio_end_ms`: 0 occurrences in the raw provider events
- completed-transcription usage: duration-based, reporting 55 seconds

The targeted second run therefore confirms that this configured deployment did
not expose a provider speech timeline under the tested normal transcription
flow. Close the timestamp question for this spike. Later WPM work should use
Azure transcript text for word information and deterministic local audio/VAD
analysis for active-speech timing. Do not use local receive offsets as a
substitute.

## Latency Observations

- first useful partial transcript: about 1.950 seconds after paced sending began
- completed transcript: about 51.529 seconds after sending began, roughly 1.393 seconds after the sample's nominal 50.137-second audio duration
- notes: Azure acknowledged the final commit at about 50.674 seconds and emitted the completed transcript about 0.855 seconds later; these local receive offsets measure latency, not when speech occurred
- paused-sample follow-up: first delta at about 1.982 seconds; completed
  transcript at about 55.465 seconds, roughly 1.440 seconds after the
  54.024-second audio duration

## Pause Behavior

- what happened during a 3–5 second pause: the controlled sample contained
  about 4.554 seconds of silence
- events emitted: no segment, VAD, or provider timing events were emitted
- transcript behavior: transcript-delta receive events had a 5.255-second gap
  spanning local receive offsets about 9.222–14.476 seconds; this is consistent
  with the known pause but remains latency evidence rather than a speech timeline

## Implications for WPM

- recommended data unit: incremental delta text reconciled against the completed transcript is viable for word counting
- recommended measurement window: retain the working active-speech window
  concept, but source its timing from deterministic local audio/VAD analysis
- recommended update trigger: delta arrival can trigger recalculation, but its local receive time must not define speech duration
- double-counting/correction risks: treat the completed transcript as authoritative; this sample only normalized one leading space, but other speech may still produce revisions
- unresolved questions: whether corrections occur in harder speech and which
  deterministic local audio/VAD approach should supply the active-speech timeline

The observed provider stream alone does not yet support the product invariant
that pauses be excluded from live WPM. Do not derive speaking duration from the
receive offsets above.

## Updated Architecture Impact

The Azure spike should now be read as the evidence that motivated the provider change.

What remains useful:

- Azure realtime text latency was acceptable for this product.
- Transcript deltas were frequent.
- Provider receive timestamps must not be used as speech timestamps.
- The tested Azure flow did not expose the speech timeline required by the WPM definition.

What is superseded:

```text
Azure transcript
+
local deterministic VAD
→ WPM
```

Current MVP direction:

```text
Deepgram Nova-3 Results
+
word-level start/end timestamps
→ deterministic WPM
```

Local VAD is now a fallback only if real microphone testing shows that Deepgram's word timeline is insufficient.
