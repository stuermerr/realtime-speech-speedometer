# M6 Session Summary Acceptance

This record covers the post-M5 completion workflow. It supplements, rather
than replaces, the live-transport checks in `07_BROWSER_LIVE_WPM_ACCEPTANCE.md`
and the product-display checks in `08_M5_PRODUCT_ACCEPTANCE.md`.

## Automated evidence

Run from the documented project directories:

```bash
cd backend && uv run pytest && uv run mypy app tests spikes
cd ../frontend && npm test && npm run typecheck && npm run build
```

The backend coverage exercises finalized-only summary construction, the exact
`summary` then `stopped` protocol order, empty and short sessions, provider
drain failures, separate inactivity acknowledgement/drain timeouts, and a
short injected inactivity timeout. Frontend coverage exercises flat backend
summary parsing, pending-summary state, reset, and completion rendering.

## Current-Chromium microphone regression

With `DEEPGRAM_API_KEY` configured, start FastAPI and Vite as described in the
README, then use current desktop Chrome or Chromium.

1. Start a presentation, speak for at least four active seconds, pause for at
   least one second, resume, and Stop immediately after a final phrase.
2. Verify the live pace remains on screen through the pause and that the final
   summary appears only after `FINALIZING…` completes.
3. Verify Average WPM, Words, Active speech, and Presentation duration are
   shown. Confirm both duration values are `m:ss` and Average WPM is rounded
   only in the UI.
4. Run an empty presentation and confirm `No speech was detected` rather than
   artificial zero metrics. Run a sub-four-second presentation and confirm the
   word/time metrics remain visible while Average WPM explains its absence.
5. Start a new presentation and verify live reading, summary, ending reason,
   and errors are reset.
6. Leave a session without recognized speech for five minutes. Confirm the
   browser stops normally, the empty completion appears, and the neutral
   inactivity explanation is visible. Repeat after speech to confirm the
   normal summary is retained.
7. Read the live WPM, scale, and final summary from 3–5 metres; verify text and
   contrast remain readable and keyboard focus remains visible.

Record the browser/Chrome version, date, result, and any diagnostic session ID
in the issue before declaring this manual regression passed. Diagnostics must
remain secret-safe: do not include transcript text, audio, credentials, or raw
provider payloads.

## Recorded result — 2026-08-25

| Evidence | Recorded value |
| --- | --- |
| Date/time | 2026-08-25 ~18:16–18:45 CEST |
| Chrome/Chromium version | Playwright headed Chrome (system Chrome) |
| Platform | Linux x86_64 |
| Backend errors | 0 |
| Browser console errors | 0 |
| WebSocket sessions | 4 accepted and completed |

### Step results

| Step | Result |
| --- | --- |
| 1. Start, speak ≥4 s, pause ≥1 s, resume, Stop | PASS — live WPM held through pause, FINALIZING… appeared before summary |
| 2. Live pace retained through pause | PASS — last WPM/status remained on screen during silent gap |
| 3. Summary metrics shown | PASS — Average WPM, Words, Active speech, Presentation duration visible; durations in m:ss format; Average WPM rounded in UI |
| 4. Empty presentation → "No speech was detected" | PASS — no artificial zero metrics; sub-four-second presentation showed word/time metrics with Average WPM unavailable |
| 5. New presentation → reset | PASS — no carried-over state; started with neutral CALCULATING display |
| 6. Inactivity stop (empty + after-speech) | PASS — 5-minute inactivity triggered "No speech was detected" with inactivity explanation; after-speech variant retained normal summary |
| 7. Readability at distance | PASS — user confirmed far-away visibility acceptable |

### Observations

- The final page state after completion shows the last WPM (e.g. 251) and
  "TOO FAST" prominently in the center, with the summary metrics below.
- The summary contains only numeric metrics; no transcript or pace-over-time
  visualization is included.
- Far-away readability was acceptable at test distances.
- Both UX observations are documented in `docs/10_UX_IMPROVEMENT_REQUESTS.md`.

## Original-case audit

| Requirement | M6 evidence |
| --- | --- |
| User starts and stops a session | Existing controls plus normal/inactivity Stop lifecycle |
| Current, large live WPM with red/green feedback | M5 pace protocol and UI remain unchanged |
| Longer pauses are handled cleanly | Active-speech timing excludes long gaps; last live value holds |
| A short post-session summary appears | Finalized-only global deterministic Summary |
| Missing permission/unsupported environment | Existing browser adapter error paths and regression coverage |
| No persistence or provider credential exposure | In-memory finalized timeline; backend-only Deepgram credential |
