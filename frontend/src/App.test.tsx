import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App, type SessionControl } from "./App";
import type { SessionAction } from "./state";

afterEach(cleanup);

describe("live speedometer product view", () => {
  it("shows raw-status feedback, retains it through pauses, and finalizes", async () => {
    const user = userEvent.setup();
    const stop = vi.fn();
    const cleanup = vi.fn();
    let emit: (action: SessionAction) => void = () => undefined;
    const createSession = vi.fn((dispatch: (action: SessionAction) => void): SessionControl => {
      emit = dispatch;
      return {
        start: async () => dispatch({ type: "listening" }),
        stop,
        cleanup,
      };
    });
    render(<App createSession={createSession} />);

    expect(screen.getByText("—")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Start presentation" }));
    expect(screen.getByText("CALCULATING…")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Start presentation" })).toBeNull();

    act(() => emit({ type: "measurement", wpm: 150.4, paceStatus: "red" }));
    expect(screen.getByText("150", { selector: ".wpm-number" })).not.toBeNull();
    expect(screen.getByText("TOO FAST", { selector: ".pace-status" })).not.toBeNull();
    expect(Number.parseFloat(screen.getByTestId("pace-marker").style.left)).toBeCloseTo(56.5);

    act(() => emit({ type: "measurement", wpm: null, paceStatus: null }));
    expect(screen.getByText("150", { selector: ".wpm-number" })).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Stop presentation" }));
    expect(stop).toHaveBeenCalledOnce();
    expect(screen.getByText("FINALIZING…")).not.toBeNull();

    act(() => emit({
      type: "summary",
      summary: {
        averageSpeakingPace: 120, finalizedWords: 8, activeSpeakingSeconds: 4,
        presentationDurationSeconds: 4,
        segments: [
          { text: "First complete chunk.", averageSpeakingPace: 120.4, paceStatus: "green" },
          { text: "Second complete chunk.", averageSpeakingPace: 90.2, paceStatus: "red" },
          { text: "Final short chunk.", averageSpeakingPace: null, paceStatus: null },
        ],
      },
    }));
    act(() => emit({ type: "stopped", reason: "user" }));
    expect(screen.getByText("Presentation complete")).not.toBeNull();
    expect(screen.queryByText("150", { selector: ".wpm-number" })).toBeNull();
    expect(screen.queryByLabelText(/Pace scale from/)).toBeNull();
    expect(screen.getByRole("heading", { name: "Pace transcript" })).not.toBeNull();
    expect(screen.getByText("First complete chunk.")).not.toBeNull();
    expect(screen.getByText("Second complete chunk.")).not.toBeNull();
    expect(screen.getByText("Final short chunk.")).not.toBeNull();
    expect(screen.getByText("On pace")).not.toBeNull();
    expect(screen.getByText("Too slow")).not.toBeNull();
    expect(screen.getByText("Pace unavailable")).not.toBeNull();
    expect(screen.getAllByTestId("compact-pace-marker")).toHaveLength(3);
    const unavailableMarker = screen.getByLabelText("Segment pace unavailable");
    expect(unavailableMarker.getAttribute("data-available")).toBe("false");
    expect(unavailableMarker.style.left).toBe("");
    const firstSegment = screen.getByText("First complete chunk.").closest(".summary-segment");
    expect(firstSegment?.querySelector(":scope > .segment-copy")?.textContent).toContain(
      "First complete chunk.",
    );
    expect(firstSegment?.querySelector(":scope > .segment-analysis")?.textContent).toContain(
      "120 WPM",
    );
    expect(
      screen.getByRole("button", { name: "Start new presentation" }),
    ).not.toBeNull();
  });

  it("keeps global metrics in an empty completion", async () => {
    const user = userEvent.setup();
    let emit: (action: SessionAction) => void = () => undefined;
    render(<App createSession={(dispatch) => {
      emit = dispatch;
      return { start: async () => dispatch({ type: "listening" }), stop: () => undefined, cleanup: () => undefined };
    }} />);
    await user.click(screen.getByRole("button", { name: "Start presentation" }));
    act(() => emit({ type: "summary", summary: {
      averageSpeakingPace: null, finalizedWords: 0, activeSpeakingSeconds: 0,
      presentationDurationSeconds: 0, segments: [],
    } }));
    act(() => emit({ type: "stopped", reason: "user" }));

    expect(screen.getByText("No speech was detected")).not.toBeNull();
    expect(screen.getByText("Average WPM")).not.toBeNull();
    expect(screen.getByText("Words")).not.toBeNull();
    expect(screen.queryByRole("heading", { name: "Pace transcript" })).toBeNull();
  });

  it("renders a long segment recap completely without capping the list", async () => {
    const user = userEvent.setup();
    let emit: (action: SessionAction) => void = () => undefined;
    render(<App createSession={(dispatch) => {
      emit = dispatch;
      return { start: async () => dispatch({ type: "listening" }), stop: () => undefined, cleanup: () => undefined };
    }} />);
    await user.click(screen.getByRole("button", { name: "Start presentation" }));
    const segments = Array.from({ length: 24 }, (_, index) => ({
      text: `Chronological segment ${index + 1}`,
      averageSpeakingPace: 120,
      paceStatus: "green" as const,
    }));
    act(() => emit({ type: "summary", summary: {
      averageSpeakingPace: 120, finalizedWords: 192, activeSpeakingSeconds: 96,
      presentationDurationSeconds: 100, segments,
    } }));
    act(() => emit({ type: "stopped", reason: "user" }));

    expect(document.querySelector("main")?.getAttribute("data-lifecycle")).toBe("completed");
    expect(document.querySelectorAll(".summary-segment")).toHaveLength(24);
    expect(screen.getByText("Chronological segment 24")).not.toBeNull();
  });

  it("uses the same completed layout for inactivity and resets into a fresh session", async () => {
    const user = userEvent.setup();
    let emit: (action: SessionAction) => void = () => undefined;
    render(<App createSession={(dispatch) => {
      emit = dispatch;
      return { start: async () => dispatch({ type: "listening" }), stop: () => undefined, cleanup: () => undefined };
    }} />);
    await user.click(screen.getByRole("button", { name: "Start presentation" }));
    act(() => emit({ type: "summary", summary: {
      averageSpeakingPace: null, finalizedWords: 1, activeSpeakingSeconds: .5,
      presentationDurationSeconds: .5,
      segments: [{ text: "Short.", averageSpeakingPace: null, paceStatus: null }],
    } }));
    act(() => emit({ type: "stopped", reason: "inactivity" }));

    expect(screen.getByText(/ended after five minutes/)).not.toBeNull();
    expect(screen.getByText("Short.")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Start new presentation" }));
    expect(screen.queryByText("Short.")).toBeNull();
    expect(screen.getByText("CALCULATING…")).not.toBeNull();
  });

  it("does not offer retry for an unsupported environment", async () => {
    const user = userEvent.setup();
    const createSession = (dispatch: (action: SessionAction) => void): SessionControl => ({
      start: async () => dispatch({
        type: "unsupported",
        message: "This browser cannot record WebM/Opus audio.",
      }),
      stop: () => undefined,
      cleanup: () => undefined,
    });
    render(<App createSession={createSession} />);

    await user.click(screen.getByRole("button", { name: "Start presentation" }));

    expect(screen.getByText("Browser not supported")).not.toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
