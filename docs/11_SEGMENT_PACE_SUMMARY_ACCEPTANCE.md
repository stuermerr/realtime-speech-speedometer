# Segment Pace Summary Acceptance

This record covers the post-MVP issue #20 enhancement. It supplements the M6
record without changing its historical observations or results.

## Automated evidence

Run from the documented project directories:

```bash
cd backend && uv run pytest && uv run mypy app tests spikes
cd ../frontend && npm test && npm run typecheck && npm run build
```

Backend coverage includes formatted final-chunk retention, empty-final
behavior, whole-chunk greedy grouping, short-tail merging, short and empty
presentations, independent segment timing, classification, summary wire shape,
normal and inactivity completion, and failed-finalization paths. Frontend
coverage includes atomic segment parsing, pending-summary/error handling,
reset, chronological rendering, rounded and directional labels, compact scale
markers, inactivity completion, hidden live UI, and accessibility labels.

## Focused current-Chromium microphone regression

Use one sufficiently long successful presentation; short, empty, inactivity,
and failure variants remain automated.

1. Start a presentation and speak long enough for Deepgram to produce multiple
   non-empty final chunks and for at least two pace segments to appear.
2. Vary pace across the presentation, then Stop. Verify `FINALIZING…` precedes
   Completed and the four global metrics remain visible.
3. Verify every spoken final chunk appears once in chronological order without
   truncation, with rounded WPM, a label, and a plausible compact marker.
4. Verify the dominant live WPM/status and full-size live scale are absent.
5. Scroll through the complete transcript without clipping.
6. Start a new presentation and verify Summary, reason, errors, segments, and
   stale live state are gone.
7. Verify zero browser-console and backend errors. Record only secret-safe
   evidence; do not record audio, transcript text, credentials, or provider
   payloads.

## Recorded result

| Evidence | Recorded value |
| --- | --- |
| Date/time | 2026-08-26 ~00:52–00:54 CEST |
| Browser | Google Chrome 149.0.7827.200, headed Playwright CLI |
| Platform | Linux x86_64 |
| Finalized words | 90 |
| Visible pace segments / compact markers | 4 / 4 |
| Completed document / viewport height | 968 px / 862 px (vertical overflow confirmed) |
| Browser console | 0 errors, 0 warnings |
| Backend errors | 0 |

### Step results

| Step | Result |
| --- | --- |
| Multiple real final chunks and segments | PASS — one ~40-second microphone presentation produced four chronological segments |
| Finalization and global metrics | PASS — Completed followed `FINALIZING…`; all four metrics were visible |
| Segment display | PASS — every segment had rounded WPM, a directional/on-pace label, and a compact marker |
| Completed hierarchy | PASS — DOM inspection confirmed no `.live-reading` and no non-compact pace scale |
| Complete scrolling | PASS — document height exceeded viewport height and content remained available vertically |
| Fresh second session | PASS — reset showed neutral `— WPM` and `CALCULATING…`, with no prior summary or segments |
| Error check | PASS — browser console and backend logs contained no errors |

No audio, transcript text, credentials, raw provider payloads, or other secrets
were retained in this record.
