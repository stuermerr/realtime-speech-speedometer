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
    });
  });

  it("moves immediately through finalizing to completed", () => {
    const listening = reduceSession(INITIAL_STATE, { type: "listening" });
    const finalizing = reduceSession(listening, { type: "stop" });

    expect(finalizing.lifecycle).toBe("finalizing");
    expect(reduceSession(finalizing, { type: "stopped" }).lifecycle).toBe(
      "completed",
    );
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
