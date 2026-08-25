import { describe, expect, it } from "vitest";

import {
  INITIAL_STATE,
  markerPosition,
  reduceSession,
} from "./state";

describe("live presentation state", () => {
  it("retains the last valid raw measurement until a new session", () => {
    const starting = reduceSession(INITIAL_STATE, { type: "start" });
    const measured = reduceSession(starting, {
      type: "measurement",
      wpm: 150.4,
      paceStatus: "red",
    });
    const unavailable = reduceSession(measured, {
      type: "measurement",
      wpm: null,
      paceStatus: null,
    });

    expect(measured.display).toEqual({ wpm: 150.4, paceStatus: "red" });
    expect(unavailable.display).toEqual(measured.display);
    expect(reduceSession(unavailable, { type: "start" })).toEqual({
      lifecycle: "starting",
      display: null,
      error: null,
      pendingSummary: null,
      completedSummary: null,
      completionReason: null,
    });
  });

  it("reveals a pending summary only when the following stopped event arrives", () => {
    const listening = reduceSession(INITIAL_STATE, { type: "listening" });
    const finalizing = reduceSession(listening, { type: "stop" });
    const pending = reduceSession(finalizing, {
      type: "summary",
      summary: { averageSpeakingPace: 120, finalizedWords: 8, activeSpeakingSeconds: 4, presentationDurationSeconds: 4, segments: [] },
    });

    expect(finalizing.lifecycle).toBe("finalizing");
    expect(pending.completedSummary).toBeNull();
    expect(reduceSession(pending, { type: "stopped", reason: "user" }).lifecycle).toBe(
      "completed",
    );
  });

  it("discards pending and completed data on failure and resets all session state", () => {
    const summary = {
      averageSpeakingPace: 120, finalizedWords: 8, activeSpeakingSeconds: 4,
      presentationDurationSeconds: 4,
      segments: [{ text: "Segment", averageSpeakingPace: 120, paceStatus: "green" as const }],
    };
    const pending = reduceSession(INITIAL_STATE, { type: "summary", summary });
    const failed = reduceSession(pending, { type: "fail", message: "failed" });
    const completed = reduceSession(pending, { type: "stopped", reason: "inactivity" });

    expect(failed.pendingSummary).toBeNull();
    expect(failed.completedSummary).toBeNull();
    expect(reduceSession(completed, { type: "start" })).toEqual({
      lifecycle: "starting", display: null, error: null, pendingSummary: null,
      completedSummary: null, completionReason: null,
    });
  });

  it("turns invalid measurement availability into a safe protocol error", () => {
    const next = reduceSession(INITIAL_STATE, {
      type: "measurement",
      wpm: 120,
      paceStatus: null,
    });

    expect(next.lifecycle).toBe("error");
    expect(next.error).toBe("The live session sent an invalid measurement.");
  });

  it("clamps only marker presentation, never the numeric measurement", () => {
    const measured = reduceSession(INITIAL_STATE, {
      type: "measurement",
      wpm: 300,
      paceStatus: "red",
    });

    expect(measured.display?.wpm).toBe(300);
    expect(markerPosition(20)).toBe(0);
    expect(markerPosition(150.4)).toBeCloseTo(56.5);
    expect(markerPosition(300)).toBe(100);
  });
});
