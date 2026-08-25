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

    act(() => emit({ type: "stopped" }));
    expect(screen.getByText("Presentation complete")).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "Start new presentation" }),
    ).not.toBeNull();
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
