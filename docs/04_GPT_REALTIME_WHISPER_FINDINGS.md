# `gpt-realtime-whisper` Mini-Spike Findings

## Current Decision Status

**Completed comparison evidence; not selected for MVP.**

This mini-spike confirmed that `gpt-realtime-whisper` did not solve the timing limitation observed with `gpt-live-transcribe`.

A later Deepgram Nova-3 spike exposed word-level audio timestamps and is now the accepted MVP provider path.

See `docs/06_DEEPGRAM_NOVA_3_FINDINGS.md` and ADR-013/ADR-014 in `docs/01_ARCHITECTURE_DECISIONS.md`.


## Status

**COMPLETED** on 2026-08-23.

This was a single, narrow comparison run. Its only question was:

> Does `gpt-realtime-whisper` expose usable audio timestamps or segment timing?

## Environment

- Azure resource: project-specific resource name omitted
- Azure region: `swedencentral`
- model: `gpt-realtime-whisper`
- model version: `2026-05-06`
- deployment type: `GlobalStandard`
- API: GA Realtime API (`/openai/v1/realtime?intent=transcription`)
- transport: server-to-server WebSocket
- session turn detection: `null`
- audio: `samples/sample_02.wav`, normalized to PCM16 mono at 24 kHz
- audio duration: 54.024 seconds
- controlled silence: about 4.554 seconds, from about 8.419 to 12.973 seconds

The existing probe code, audio normalization, 100 ms pacing, final client
commit, raw-event capture, and completion handling were reused unchanged. Only
the configured deployment and fixed sample path were overridden for the run.

## Availability Check

The Azure resource advertised this deployable model:

| Field | Observed value |
| --- | --- |
| model | `gpt-realtime-whisper` |
| version | `2026-05-06` |
| format | `OpenAI` |
| SKU | `GlobalStandard` |

The comparison deployment provisioned successfully before the probe.

The [Azure GA Realtime REST reference](https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/realtime)
also states that `gpt-realtime-whisper` transcription sessions require
`turn_detection` to be `null` and do not support VAD. The run below verifies
the actual event behavior of this deployment rather than treating that
documentation statement as timestamp evidence.

## Events Observed

The complete raw capture contained 190 events:

| Event type | Count |
| --- | ---: |
| `session.created` | 1 |
| `session.updated` | 1 |
| `conversation.item.input_audio_transcription.delta` | 184 |
| `input_audio_buffer.committed` | 1 |
| `conversation.item.added` | 1 |
| `conversation.item.done` | 1 |
| `conversation.item.input_audio_transcription.completed` | 1 |

The final transcript completed successfully. Concatenating all 184 transcript
deltas matched the final transcript after trimming one leading space.

## Timestamp and VAD Result

No usable provider speech timeline was present:

- word timestamp fields: not observed
- `conversation.item.input_audio_transcription.segment`: 0 events
- exact `start` / `end` fields: 0 occurrences
- exact `audio_start_ms` / `audio_end_ms` fields: 0 occurrences
- `input_audio_buffer.speech_started`: 0 events
- `input_audio_buffer.speech_stopped`: 0 events

The accepted `session.updated` event confirmed `turn_detection: null`. The
completed-transcription event contained only duration-based usage timing:

```json
{
  "usage": {
    "seconds": 55,
    "type": "duration"
  }
}
```

This describes billed/processed audio duration, not active speech intervals.

The largest local receive-time gap between transcript deltas was about 5.008
seconds, from offsets 9.204 to 14.212 seconds. It is consistent with the known
pause, but remains network/model receive-time evidence and cannot be used as an
audio speech timeline.

## A/B Result

Both models were tested with the same paused sample and the same probe flow.

| Capability | `gpt-live-transcribe` | `gpt-realtime-whisper` |
| --- | ---: | ---: |
| Streaming transcript deltas | yes (174) | yes (184) |
| Final transcript | yes | yes |
| Word timestamps | not observed | not observed |
| Segment events/timestamps | 0 | 0 |
| VAD speech-start/stop events | 0 | 0 |
| Pause visible in receive-time gap | yes, indirectly | yes, indirectly |

Different delta counts are not evidence of better timing granularity. Both
models produced append-only text deltas for this sample and neither exposed the
audio positions needed for deterministic active-speech duration.

## Conclusion

Close the provider-timestamp comparison. Under the tested GA Azure Realtime
transcription flow, neither model supplies a usable speech timeline for live
WPM.

Use separate responsibilities for later implementation:

```text
Azure realtime transcription -> incremental/final word information
Local deterministic audio VAD -> active-speech timeline
Both inputs                  -> deterministic rolling WPM
```

Do not infer speech duration from transcript event receive times or from the
completed event's duration-based usage value.

## Updated Architecture Impact

Close the Azure model comparison.

There is no current reason to add a third Azure/OpenAI transcription experiment for the MVP.

The evidence chain is:

```text
gpt-live-transcribe
→ realtime text yes, usable speech timeline no

gpt-realtime-whisper
→ realtime text yes, usable speech timeline no

Deepgram Nova-3
→ realtime text yes, word-level audio timestamps yes
```

Therefore:

- keep the Azure spikes as documented technical evidence;
- use Deepgram Nova-3 for the MVP;
- do not implement the previously proposed Azure + local-VAD alignment path unless the provider decision changes again.
