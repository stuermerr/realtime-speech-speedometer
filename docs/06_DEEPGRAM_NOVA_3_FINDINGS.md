# Deepgram Nova-3 Comparison Spike Findings

## Status

**COMPLETED — Deepgram Nova-3 accepted as the MVP realtime STT provider.**

The comparison produced the missing timing evidence required by the Speech Speedometer and directly changed the architecture.

Current decisions:

- ADR-013 — use Deepgram Nova-3 for MVP realtime STT;
- ADR-014 — use Deepgram word timestamps as the primary WPM timeline;
- ADR-015 — maintain finalized history plus a replaceable interim tail.

The earlier Azure-provider and local-VAD decisions are retained as historical evidence but are superseded for the MVP.

## Environment

- model: `nova-3`
- language: German (`de`)
- transport: Deepgram `/v1/listen` WebSocket
- audio: `samples/sample_02.wav`, normalized to linear PCM16 mono at 24 kHz
- audio duration: 54.024 seconds
- paced run duration: 54.682 seconds
- interim results: enabled
- VAD events: enabled
- endpointing: 600 ms
- utterance end: 1,000 ms

The probe used the direct WebSocket protocol so the provider events could be
observed without an SDK event model. The implementation was based on the
official Deepgram streaming API, endpointing, utterance-end, and keep-alive
documentation. `docs/05-deepgram-model-usage-example.py` was treated only as a
reference as requested.

## Event Types Observed

| Event type | Count | Important fields |
| --- | ---: | --- |
| `Results` | 71 | `is_final`, `speech_final`, `start`, `duration`, `channel.alternatives[].transcript`, `words[].start/end` |
| `SpeechStarted` | 13 | `timestamp` |
| `UtteranceEnd` | 4 | `last_word_end` |
| `Metadata` | 1 | connection/request metadata |

Of the 71 `Results` events, 58 were interim and 13 were final.

The 58 interim Results were also inspected at the word level. Fifty-three had
a nonempty transcript with fully timed words, while five were empty Results.
Across successive hypotheses, Deepgram grew the word list but also revised
word text, membership, and timestamps; some later hypotheses dropped words
that had appeared in an earlier interim result. A sanitized deterministic
sequence preserving these behaviors lives at
`backend/tests/fixtures/deepgram_realtime_results.json`.

## Required Checks

### Interim transcript

**Yes.** Non-final `Results` events arrived during paced playback. Final
results divided the sample into 13 transcript chunks.

### Word start and end timestamps

**Yes.** Final results contained word-level `start` and `end` values on the
audio timeline. The final chunks contained 129 recognized words in total.

Representative sanitized shape:

```json
{
  "type": "Results",
  "is_final": true,
  "speech_final": false,
  "start": 29.39,
  "duration": 4.82,
  "channel": {
    "alternatives": [
      {
        "transcript": "…",
        "words": [
          {"word": "…", "start": 29.72, "end": 30.12}
        ]
      }
    ]
  }
}
```

### SpeechStarted

**Yes.** Thirteen events were observed with audio-timeline timestamps. Their
timestamps ranged from 0.40 to 52.56 seconds.

The event count is much higher than the one intentionally long pause in the
sample. Deepgram's VAD treated several short natural gaps as new speech starts,
so `SpeechStarted` should be treated as useful boundary evidence rather than a
perfect semantic utterance count.

### speech_final and UtteranceEnd

**Both were observed, but their behavior differs.**

- All 13 final transcript chunks had `is_final: true`.
- Only the last final chunk had `speech_final: true`; the earlier 12 had
  `speech_final: false` despite 600 ms endpointing.
- Four `UtteranceEnd` events were emitted, with `last_word_end` values 8.47,
  15.50, 20.84, and 33.23 seconds.

Therefore `is_final`, `speech_final`, and `UtteranceEnd` must not be treated as
synonyms. Live word accumulation can use final `Results`; pause handling should
use word/VAD timing rather than waiting exclusively for `speech_final`.

### Known pause

**Yes.** The largest gap between consecutive final words was 4.87 seconds,
between a word ending at 8.47 seconds and the next word starting at 13.34
seconds. This closely matches the known approximately 4.55-second pause; the
difference reflects the silence around the spoken word boundaries.

The next-largest final-word gaps were 1.14, 1.02, 0.87, and 0.82 seconds, so the
intentional long pause is clearly separable in this sample.

## Implications for Speech Speedometer

Nova-3 exposes the two data classes the WPM definition needs in one stream:

1. incremental/finalized recognized text;
2. provider-derived audio-timeline word boundaries.

The controlled spike also showed enough realtime activity for an event-driven UI approach:

- 71 `Results` events over 54.024 seconds;
- 58 interim results;
- 13 final results.

The final transcript chunks contained 129 recognized words with word-level `start` and `end` timestamps.

This simplifies the current MVP architecture to:

```text
Browser audio
    ↓
FastAPI
    ↓
Deepgram Nova-3
    ↓
Results + word timestamps
    ↓
deterministic rolling WPM
    ↓
frontend
```

### Current timing decision

Use `words[].start` and `words[].end` as the primary speech timeline.

Do not use local network receive offsets as speech timing.

Do not treat `SpeechStarted`, `speech_final`, `is_final`, and `UtteranceEnd` as interchangeable concepts.

### Current pause direction

Use gaps between recognized word intervals as the primary evidence for pauses.

The controlled long pause was visible as a 4.87-second gap between final words.

The exact pause threshold is still a product/algorithm parameter and must be validated with real microphone sessions.

### Current transcript-state direction

Maintain:

```text
finalized word history
+
replaceable current interim tail
```

Final results are authoritative.

The spike proved frequent interim `Results`, and follow-up inspection found
fully timed words in 53 of 58 interim Results. Those hypotheses remain
replaceable because their text, membership, and timestamps were all observed
to change. The application therefore normalizes each useful Results event as
one atomic hypothesis; downstream session state must replace the current
interim tail rather than accumulate interim events.

### Removed complexity

Do not implement these for the MVP unless later evidence requires them:

- local deterministic VAD;
- Azure transcript/local-VAD alignment;
- artificial periodic Azure commits used only to create timing boundaries.

The WPM calculation and 115–150 classification remain deterministic application code.

## Architecture Decision

**Deepgram Nova-3 is selected for the MVP.**

The reason is not brand preference or transcription accuracy alone.

The reason is architectural fit:

> Deepgram returns the recognized words and the audio-timeline timing needed by the product in the same realtime stream, eliminating an otherwise separate speech-timing subsystem.

This is the evidence supporting the provider decision.
## Reproduction

From `backend/`:

```bash
uv run python -m spikes.run_deepgram_transcription
```

The command reads `DEEPGRAM_API_KEY` from the process environment or the
explicit `backend/.env` file, with the process environment taking precedence.
It writes a complete local-only
report to `backend/spikes/.artifacts/`; the directory remains ignored.

## Sources

- [Deepgram live streaming API](https://developers.deepgram.com/reference/speech-to-text/listen-streaming)
- [Deepgram endpointing and interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results)
- [Deepgram utterance end](https://developers.deepgram.com/docs/utterance-end)
- [Deepgram audio keep-alive and timestamp semantics](https://developers.deepgram.com/docs/audio-keep-alive)
