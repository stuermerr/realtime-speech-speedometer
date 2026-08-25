import { describe, expect, it, vi } from "vitest";

import {
  BrowserSession,
  type BrowserRuntime,
  type RecorderPort,
  type SocketPort,
} from "./browserSession";
import type { SessionAction } from "./state";

class FakeRecorder extends EventTarget implements RecorderPort {
  readonly mimeType = "audio/webm;codecs=opus";
  state: RecordingState = "inactive";

  start(timeslice?: number): void {
    expect(timeslice).toBe(250);
    this.state = "recording";
  }

  chunk(value: string): void {
    this.dispatchEvent(new MessageEvent("dataavailable", { data: new Blob([value]) }));
  }

  stop(): void {
    this.state = "inactive";
    this.dispatchEvent(new MessageEvent("dataavailable", { data: new Blob(["final"]) }));
    this.dispatchEvent(new Event("stop"));
  }
}

class FakeSocket extends EventTarget implements SocketPort {
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readyState = 0;
  binaryType: BinaryType = "blob";
  readonly sent: (string | ArrayBuffer)[] = [];
  closeCount = 0;

  open(): void {
    this.readyState = this.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  send(data: string | ArrayBuffer): void {
    this.sent.push(data);
  }

  close(): void {
    this.closeCount += 1;
    this.readyState = 3;
  }
}

function runtime(options?: { supported?: boolean; autoOpen?: boolean }): {
  value: BrowserRuntime;
  socket: FakeSocket;
  recorder: FakeRecorder;
  stopTrack: ReturnType<typeof vi.fn>;
  getUserMedia: ReturnType<typeof vi.fn>;
} {
  const socket = new FakeSocket();
  const recorder = new FakeRecorder();
  const stopTrack = vi.fn();
  const getUserMedia = vi.fn(async () => ({
    getTracks: () => [{ stop: stopTrack }],
  }));
  const value: BrowserRuntime = {
    capabilities: () => options?.supported === false
      ? "This browser cannot record WebM/Opus audio."
      : null,
    getUserMedia,
    createSocket: () => {
      if (options?.autoOpen !== false) queueMicrotask(() => socket.open());
      return socket;
    },
    createRecorder: () => recorder,
    setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
    clearTimer: (timer) => window.clearTimeout(timer),
  };
  return { value, socket, recorder, stopTrack, getUserMedia };
}

async function settle(): Promise<void> {
  await new Promise((resolve) => window.setTimeout(resolve, 0));
}

describe("browser session adapter", () => {
  it("reports unsupported capabilities before requesting the microphone", async () => {
    const browser = runtime({ supported: false });
    const events: SessionAction[] = [];

    await new BrowserSession(events.push.bind(events), browser.value).start();

    expect(events).toEqual([
      {
        type: "unsupported",
        message: "This browser cannot record WebM/Opus audio.",
      },
    ]);
    expect(browser.getUserMedia).not.toHaveBeenCalled();
  });

  it("delivers the final recorder chunk before Stop and cleans up once", async () => {
    const browser = runtime();
    const events: SessionAction[] = [];
    const session = new BrowserSession(events.push.bind(events), browser.value);
    await session.start();

    browser.recorder.chunk("first");
    session.stop();
    await settle();

    expect(browser.socket.sent).toHaveLength(3);
    expect(browser.socket.sent[0]).toBeInstanceOf(ArrayBuffer);
    expect(browser.socket.sent[1]).toBeInstanceOf(ArrayBuffer);
    expect(browser.socket.sent[2]).toBe(JSON.stringify({ type: "stop" }));

    browser.socket.dispatchEvent(
      new MessageEvent("message", { data: JSON.stringify({ type: "stopped" }) }),
    );
    await settle();
    session.cleanup();

    expect(events).toContainEqual({ type: "listening" });
    expect(events).toContainEqual({ type: "stopped" });
    expect(browser.stopTrack).toHaveBeenCalledTimes(1);
    expect(browser.socket.closeCount).toBe(1);
  });

  it("fails a connection that does not open within ten seconds", async () => {
    vi.useFakeTimers();
    const browser = runtime({ autoOpen: false });
    const events: SessionAction[] = [];
    const start = new BrowserSession(events.push.bind(events), browser.value).start();

    await vi.advanceTimersByTimeAsync(10_000);
    await start;

    expect(events.at(-1)).toEqual({
      type: "fail",
      message: "Could not connect to the live service in time.",
    });
    expect(browser.stopTrack).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("reports microphone denial as recoverable without opening a socket", async () => {
    const browser = runtime();
    browser.value.getUserMedia = vi.fn(async () => {
      throw new DOMException("private detail", "NotAllowedError");
    });
    const createSocket = vi.spyOn(browser.value, "createSocket");
    const events: SessionAction[] = [];

    await new BrowserSession(events.push.bind(events), browser.value).start();

    expect(events.at(-1)).toEqual({
      type: "fail",
      message: "Microphone permission was denied. Allow access and try again.",
    });
    expect(createSocket).not.toHaveBeenCalled();
  });

  it("rejects invalid WPM/status combinations and releases resources", async () => {
    const browser = runtime();
    const events: SessionAction[] = [];
    await new BrowserSession(events.push.bind(events), browser.value).start();

    browser.socket.dispatchEvent(new MessageEvent("message", {
      data: JSON.stringify({ type: "measurement", wpm: 120, pace_status: null }),
    }));

    expect(events.at(-1)).toEqual({
      type: "fail",
      message: "The live session sent an invalid message.",
    });
    expect(browser.stopTrack).toHaveBeenCalledTimes(1);
    expect(browser.socket.closeCount).toBe(1);
  });
});
