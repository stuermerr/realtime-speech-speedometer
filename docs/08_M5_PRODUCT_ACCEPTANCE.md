# M5 Live Speedometer Product Acceptance

This procedure validates the product UI introduced for issue #16. It
complements the browser-to-Deepgram transport evidence in
`docs/07_BROWSER_LIVE_WPM_ACCEPTANCE.md`.

## Automated acceptance

Run from the repository root:

```bash
uv run --directory backend --locked pytest
uv run --directory backend --locked mypy app tests spikes
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

The focused suites cover:

- backend classification at 115, 150, and 150.4 WPM plus null availability;
- the WebSocket measurement shape and WPM/status invariant;
- reducer lifecycle, retained unavailable measurements, fresh-session reset,
  marker-only clamping, and protocol failure;
- browser capability checks, microphone denial, connection timeout, ordered
  final audio, Stop, invalid protocol cleanup, and idempotent cleanup;
- visible calculating, red/directional 150.4 feedback, finalizing, completion,
  and unsupported behavior.

## Presentation check

Build the frontend, start FastAPI, and open `http://localhost:8000/` in current
desktop Chrome. Check both 1280×720 and 1920×1080 fullscreen:

1. WPM is the dominant element and status is the second visual priority.
2. The 60–220 scale, 115/150 boundaries, direction labels, and marker are clear.
3. No page overflow or clipped controls appear.
4. Tab focuses Start and Stop with a visible focus ring.
5. Green/red feedback always includes textual status and direction.

## Real-microphone product flow

With a valid `DEEPGRAM_API_KEY` and physical microphone:

1. Start and speak until WPM is available.
2. Pause for at least five seconds; verify the last WPM/status/marker remains.
3. Resume and verify the same session updates.
4. Stop immediately after a final phrase; verify `FINALIZING…` appears before
   `Presentation complete`.
5. Start a second presentation; verify the neutral `— WPM` / `CALCULATING…`
   display and no carried state.
6. Repeat with denied microphone permission, backend interruption, and an
   unsupported capability; verify cleanup and the appropriate retry behavior.
7. Confirm WPM/status readability from about 3 m at 1280×720 and about 5 m at
   1920×1080, with direction and marker readable from about 3 m.

## Recorded result — 2026-08-25 CEST

| Check | Result |
| --- | --- |
| Chrome/platform | Chrome 149.0.7827.200; Linux 6.17.0-35-generic x86_64 |
| 1280×720 layout | PASS — no vertical overflow; WPM 165.6 px; status 48 px; scale and controls visible |
| 1920×1080 layout | PASS — no vertical overflow; WPM 270 px; status 72 px; scale and controls visible |
| Keyboard focus | PASS — Tab reaches Start; visible 3 px white outline with 4 px offset |
| Console | PASS — no application errors or warnings |
| Automated backend/frontend checks | PASS — see final implementation verification output |
| Physical-distance readability | NOT RUN — requires an observer at the stated distances |
| Rewritten adapter with real microphone | NOT RUN — requires physical speech input and provider usage |

The earlier transport adapter passed a real-microphone Start → speech → pause →
resume → Stop → second-session run, recorded in
`docs/07_BROWSER_LIVE_WPM_ACCEPTANCE.md`. That evidence remains valid for the
unchanged backend transport and provider drain, but it is not reported as a
pass for the rewritten M5 React browser adapter. Complete the two `NOT RUN`
rows before closing issue #16.
