# Captured timing fixture

`deepgram_timing_words.json` is a sanitized, provider-neutral excerpt from the
final recognized words in the local Deepgram Nova-3 spike report
`backend/spikes/.artifacts/20260823T214148Z-deepgram-report.json`.

The fixture retains the first 46 final word records and renames the provider's
`word` field to `text`. It intentionally retains only `text`, `start`, and `end`;
provider event envelopes, request metadata, confidence scores, punctuated
forms, credentials, and the rest of the transcript are excluded. The source
artifact remains ignored and local-only.

The explicit regression checkpoints are independently reviewable from the
fixture with the default 1-second pause threshold:

- Records 1–24 span 0.16–8.47 seconds with only sub-threshold gaps: 24 words,
  8.31 active seconds, and `24 × 60 ÷ 8.31 = 173.2851985559567` WPM.
- Records 25–32 add 2.16 active seconds after the excluded 4.87-second pause:
  32 words, 10.47 active seconds, and `32 × 60 ÷ 10.47 =
  183.3810888252149` WPM.
- After all records, the 10-second rolling window starts at record 11. Its
  three active intervals are 4.32, 2.16, and 4.32 seconds; the 4.87- and
  1.02-second gaps are excluded. That gives 36 words, 10.80 active seconds,
  and `36 × 60 ÷ 10.80 = 200` WPM over audio bounds 4.15–20.84 seconds.

`deepgram_realtime_results.json` is a smaller sanitized sequence derived from
the same local capture's event shapes. It preserves only the fields required
by the application parser and demonstrates interim growth, text and timestamp
revision, a dropped interim word, an empty Results event, and finalization.
Names were replaced and confidence, request metadata, and other provider-only
fields were removed. Tests use the sequence as replaceable hypotheses; they do
not append it into a transcript or contact Deepgram.
