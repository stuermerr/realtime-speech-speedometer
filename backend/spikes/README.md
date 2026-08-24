# Realtime transcription spike

Run the fixed repository sample from `backend/`:

```bash
uv run python -m spikes.run_realtime_transcription
```

The command reads Azure settings from the process environment or the explicit
`backend/.env` file, with the process environment taking precedence. It converts
`samples/sample_01.wav` from 44.1 kHz PCM16 mono to 24 kHz PCM16 mono and
streams it in paced 100 ms chunks. It waits up to 10 seconds for a completed
transcript after playback. Override that bound with
`TRANSCRIPTION_SPIKE_COMPLETION_TIMEOUT_SECONDS` when needed.

Each run writes complete provider events and a timing-oriented report under
the ignored `backend/spikes/.artifacts/` directory. Review and sanitize any
representative evidence before copying it into
`docs/03_GPT_LIVE_TRANSCRIBE_FINDINGS.md`.

For the final timestamp check, use a sample with speech, 3–5 seconds of
silence, and more speech. In the generated report, check `event_types` for
`conversation.item.input_audio_transcription.segment`. If present, compare its
reported `start` and `end` seconds with the known speech boundaries in the WAV;
the event's `id`, `text`, and `speaker` fields are retained alongside them.
Also check for `audio_start_ms` and `audio_end_ms` on other event types. A
second run without any of these timing fields closes this spike question for
the configured deployment; local event receive offsets are latency evidence,
not speech timestamps.

## Deepgram Nova-3 comparison

Run the paused German sample from `backend/`:

```bash
uv run python -m spikes.run_deepgram_transcription
```

The comparison probe reads `DEEPGRAM_API_KEY`, streams
`samples/sample_02.wav` in realtime through the reusable application
connection, and requests Nova-3 interim results, word timestamps, VAD events,
600 ms endpointing, and utterance-end events. WAV normalization, realtime
pacing, receive-time capture, and report generation remain isolated here in
spike tooling. The probe writes a local report under `.artifacts/`, including
the live WPM measurements and largest gaps between final words. WPM is derived
from reconciled provider word timestamps, while receive times remain latency
evidence only.

## Follow-up manual scenarios

The fixed sample proves the first transport path. After that, collect separate
evidence for continuous speech, fast speech, a 3–5 second pause, hesitation or
correction, German speech, and optionally English speech. Do not add alternate
input handling to this fixed-sample runner merely to execute that matrix.
