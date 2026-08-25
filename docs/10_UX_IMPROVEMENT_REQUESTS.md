# UX Improvement Requests

Captured from M6 manual acceptance testing on 2026-08-25.

## 1. Summary: add visual pace recap alongside numeric metrics

**Current behavior:** After completion, `SessionSummaryView` renders only
Average WPM, Words, Active speech, and Presentation duration. No transcript
text, pace graph, or per-word timing visualization.

**User expectation:** The summary should include a visual representation of
the session — at minimum a WPM-over-time line chart or a color-coded pace
timeline showing which segments were on pace, too slow, and too fast. A
transcript with per-utterance timing would be a further enhancement.

**Rationale:** A rhetoric-training tool's value is in helping users see
*where* they were fast or slow, not just the average. The average alone does
not tell the user what to improve.

**Scope considerations:**

- The backend does not persist transcript text (memory-only, no storage).
Adding a transcript to the summary would require either storing it in the
session state or streaming it back as a finalization payload.
- A WPM-over-time chart only needs the array of `WpmMeasurement` values
already sent during the session. The frontend could accumulate these and
render a simple SVG/canvas chart in the summary.
- This is a feature request, not a bug.



## 2. Post-completion: separate finished page, de-emphasize stale live WPM

**Current behavior:** After completion, the live-reading section still
displays the last WPM number (e.g. 251) and pace status (e.g. "TOO FAST")
prominently in the center of the page. The `pacePresentation` function
returns "COMPLETE" / "Your final pace is held above" — so the status text
updates, but the big WPM number and red/green styling remain.

**User expectation:** A dedicated finished state that:

- Shows the summary metrics (and visual recap, per item 1) as the primary
content.
- Removes or de-emphasizes the stale live WPM from the center.
- Keeps the "Start new presentation" button accessible.

**Rationale:** After a presentation ends, the last WPM is historical data,
not live feedback. Displaying it at the same visual weight as the live reading
confuses the interface's hierarchy. The user should see a clear "session
ended" state, not a frozen snapshot of the live view.

**Implementation sketch:**

- When `lifecycle === "completed"`, hide or collapse the `live-reading` section.
- Expand the `SessionSummaryView` to fill the primary content area.
- Optionally retain the last WPM as a small "final reading" label within the  
summary rather than the dominant element.

