import type { PaceStatus, SessionAction } from "./state";

const MIME_TYPE = "audio/webm;codecs=opus";
const CHUNK_MILLISECONDS = 250;
const CONNECTION_TIMEOUT_MILLISECONDS = 10_000;

interface TrackPort {
  stop(): void;
}

interface StreamPort {
  getTracks(): TrackPort[];
}

export interface RecorderPort extends EventTarget {
  readonly mimeType: string;
  readonly state: RecordingState;
  start(timeslice?: number): void;
  stop(): void;
}

export interface SocketPort extends EventTarget {
  readonly OPEN: number;
  readonly CLOSING: number;
  readonly readyState: number;
  binaryType: BinaryType;
  send(data: string | ArrayBuffer): void;
  close(): void;
}

export interface BrowserRuntime {
  capabilities(): string | null;
  getUserMedia(): Promise<StreamPort>;
  createSocket(): SocketPort;
  createRecorder(stream: StreamPort): RecorderPort;
  setTimer(callback: () => void, milliseconds: number): number;
  clearTimer(timer: number): void;
}

type Dispatch = (action: SessionAction) => void;

export class BrowserSession {
  private stream: StreamPort | null = null;
  private socket: SocketPort | null = null;
  private recorder: RecorderPort | null = null;
  private sendChain: Promise<void> = Promise.resolve();
  private cleaned = false;

  constructor(
    private readonly dispatch: Dispatch,
    private readonly runtime: BrowserRuntime = browserRuntime,
  ) {}

  async start(): Promise<void> {
    const unsupportedReason = this.runtime.capabilities();
    if (unsupportedReason !== null) {
      this.dispatch({ type: "unsupported", message: unsupportedReason });
      return;
    }

    try {
      this.stream = await this.runtime.getUserMedia();
      this.socket = this.runtime.createSocket();
      this.socket.binaryType = "arraybuffer";
      await this.waitForConnection(this.socket);
      if (this.cleaned) return;

      this.socket.addEventListener("message", this.handleMessage);
      this.socket.addEventListener("close", this.handleClose);
      this.recorder = this.runtime.createRecorder(this.stream);
      this.recorder.addEventListener("dataavailable", this.handleAudio);
      this.recorder.addEventListener("error", this.handleRecorderError);
      this.recorder.addEventListener("stop", this.handleRecorderStop);
      this.recorder.start(CHUNK_MILLISECONDS);
      this.dispatch({ type: "listening" });
    } catch (error) {
      this.fail(startErrorMessage(error));
    }
  }

  stop(): void {
    if (this.cleaned || this.recorder?.state !== "recording") return;
    this.recorder.stop();
  }

  cleanup(): void {
    if (this.cleaned) return;
    this.cleaned = true;
    if (this.recorder?.state !== "inactive") this.recorder?.stop();
    this.releaseMicrophone();
    if (this.socket !== null && this.socket.readyState < this.socket.CLOSING) {
      this.socket.close();
    }
    this.socket = null;
    this.recorder = null;
  }

  private waitForConnection(socket: SocketPort): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = this.runtime.setTimer(() => {
        reject(new Error("Could not connect to the live service in time."));
      }, CONNECTION_TIMEOUT_MILLISECONDS);
      const opened = () => {
        this.runtime.clearTimer(timer);
        resolve();
      };
      const failed = () => {
        this.runtime.clearTimer(timer);
        reject(new Error("Could not connect to the live service."));
      };
      socket.addEventListener("open", opened, { once: true });
      socket.addEventListener("error", failed, { once: true });
    });
  }

  private readonly handleAudio = (event: Event): void => {
    if (this.cleaned) return;
    const blob = (event as MessageEvent<Blob>).data;
    if (!(blob instanceof Blob) || blob.size === 0) return;
    this.sendChain = this.sendChain
      .then(async () => {
        if (this.cleaned) return;
        const socket = this.socket;
        if (socket === null || socket.readyState !== socket.OPEN) {
          throw new Error("The live connection is unavailable.");
        }
        socket.send(await blob.arrayBuffer());
      })
      .catch((error: unknown) => this.fail(errorMessage(error)));
  };

  private readonly handleRecorderStop = (): void => {
    if (!this.cleaned) void this.finishStop();
  };

  private async finishStop(): Promise<void> {
    await this.sendChain;
    if (this.cleaned) return;
    const socket = this.socket;
    if (socket === null || socket.readyState !== socket.OPEN) {
      this.fail("The live connection closed before Stop completed.");
      return;
    }
    socket.send(JSON.stringify({ type: "stop" }));
    this.releaseMicrophone();
  }

  private readonly handleMessage = (event: Event): void => {
    try {
      const message = parseBackendMessage((event as MessageEvent<unknown>).data);
      if (message.type === "measurement") {
        this.dispatch({
          type: "measurement",
          wpm: message.wpm,
          paceStatus: message.paceStatus,
        });
        return;
      }
      if (message.type === "stopped") {
        this.dispatch({ type: "stopped" });
        this.cleanup();
        return;
      }
      this.fail(message.message);
    } catch {
      this.fail("The live session sent an invalid message.");
    }
  };

  private readonly handleClose = (): void => {
    if (!this.cleaned) this.fail("The live connection closed unexpectedly.");
  };

  private readonly handleRecorderError = (): void => {
    this.fail("The browser could not continue recording audio.");
  };

  private fail(message: string): void {
    if (this.cleaned) return;
    this.dispatch({ type: "fail", message });
    this.cleanup();
  }

  private releaseMicrophone(): void {
    if (this.stream === null) return;
    for (const track of this.stream.getTracks()) track.stop();
    this.stream = null;
  }
}

type BackendMessage =
  | {
      readonly type: "measurement";
      readonly wpm: number | null;
      readonly paceStatus: PaceStatus | null;
    }
  | { readonly type: "stopped" }
  | { readonly type: "error"; readonly message: string };

function parseBackendMessage(raw: unknown): BackendMessage {
  if (typeof raw !== "string") throw new Error("Message must be text");
  const value: unknown = JSON.parse(raw);
  if (!isRecord(value) || typeof value.type !== "string") {
    throw new Error("Message must be an object");
  }
  if (value.type === "stopped") return { type: "stopped" };
  if (value.type === "error" && typeof value.message === "string") {
    return { type: "error", message: value.message };
  }
  if (value.type !== "measurement") throw new Error("Unknown message");

  const wpm = value.wpm;
  const paceStatus = value.pace_status;
  if (
    !(wpm === null || (typeof wpm === "number" && Number.isFinite(wpm))) ||
    !(paceStatus === null || paceStatus === "green" || paceStatus === "red") ||
    (wpm === null) !== (paceStatus === null)
  ) {
    throw new Error("Invalid measurement");
  }
  return { type: "measurement", wpm, paceStatus };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function startErrorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "Microphone permission was denied. Allow access and try again.";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "No microphone is available. Connect one and try again.";
  }
  return errorMessage(error, "Could not start the microphone session.");
}

function errorMessage(error: unknown, fallback = "Live session failed."): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

const browserRuntime: BrowserRuntime = {
  capabilities: () => {
    if (!window.isSecureContext) return "Microphone capture requires localhost or HTTPS.";
    if (!navigator.mediaDevices?.getUserMedia) return "Microphone access is unavailable in this browser.";
    if (typeof window.MediaRecorder === "undefined") return "Audio recording is unavailable in this browser.";
    if (typeof window.WebSocket === "undefined") return "WebSockets are unavailable in this browser.";
    if (!MediaRecorder.isTypeSupported(MIME_TYPE)) return "This browser cannot record WebM/Opus audio.";
    return null;
  },
  getUserMedia: () => navigator.mediaDevices.getUserMedia({ audio: true, video: false }),
  createSocket: () => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return new WebSocket(`${protocol}//${window.location.host}/ws/live`);
  },
  createRecorder: (stream) => new MediaRecorder(stream as MediaStream, { mimeType: MIME_TYPE }),
  setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimer: (timer) => window.clearTimeout(timer),
};
