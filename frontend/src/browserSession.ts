import type { PaceStatus, SessionAction, SessionSummary, SummarySegment } from "./state";

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
  private cancelConnection: (() => void) | null = null;

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
      const stream = await this.runtime.getUserMedia();
      if (this.cleaned) {
        stopStream(stream);
        return;
      }
      this.stream = stream;
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
      if (this.cleaned) return;
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
    this.cancelConnection?.();
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
      let timer: number | null = null;
      let settled = false;
      const finish = (complete: () => void): void => {
        if (settled) return;
        settled = true;
        if (timer !== null) this.runtime.clearTimer(timer);
        socket.removeEventListener("open", opened);
        socket.removeEventListener("error", failed);
        socket.removeEventListener("close", closed);
        if (this.cancelConnection === cancelled) this.cancelConnection = null;
        complete();
      };
      const opened = () => finish(resolve);
      const failed = () => finish(() => reject(
        new Error("Could not connect to the live service."),
      ));
      const closed = () => finish(() => reject(
        new Error("The live connection closed while starting."),
      ));
      const cancelled = () => finish(() => reject(
        new Error("Live session startup was cancelled."),
      ));
      this.cancelConnection = cancelled;
      socket.addEventListener("open", opened, { once: true });
      socket.addEventListener("error", failed, { once: true });
      socket.addEventListener("close", closed, { once: true });
      timer = this.runtime.setTimer(() => finish(() => reject(
        new Error("Could not connect to the live service in time."),
      )), CONNECTION_TIMEOUT_MILLISECONDS);
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
        this.dispatch({ type: "stopped", reason: message.reason });
        this.cleanup();
        return;
      }
      if (message.type === "summary") {
        this.dispatch({ type: "summary", summary: message.summary });
        return;
      }
      if (message.type === "stop_requested") {
        this.dispatch({ type: "stop" });
        this.stop();
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
    stopStream(this.stream);
    this.stream = null;
  }
}

type BackendMessage =
  | {
      readonly type: "measurement";
      readonly wpm: number | null;
    readonly paceStatus: PaceStatus | null;
  }
  | { readonly type: "summary"; readonly summary: SessionSummary }
  | { readonly type: "stopped"; readonly reason: "user" | "inactivity" }
  | { readonly type: "stop_requested"; readonly reason: "inactivity" }
  | { readonly type: "error"; readonly message: string };

function parseBackendMessage(raw: unknown): BackendMessage {
  if (typeof raw !== "string") throw new Error("Message must be text");
  const value: unknown = JSON.parse(raw);
  if (!isRecord(value) || typeof value.type !== "string") {
    throw new Error("Message must be an object");
  }
  if (value.type === "stopped" && (value.reason === "user" || value.reason === "inactivity")) {
    return { type: "stopped", reason: value.reason };
  }
  if (value.type === "stop_requested" && value.reason === "inactivity") {
    return { type: "stop_requested", reason: "inactivity" };
  }
  if (value.type === "error" && typeof value.message === "string") {
    return { type: "error", message: value.message };
  }
  if (value.type === "summary") {
    const summary = parseSummary(value);
    return { type: "summary", summary };
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

function parseSummary(value: Record<string, unknown>): SessionSummary {
  const averageSpeakingPace = value.average_speaking_pace;
  const finalizedWords = value.finalized_words;
  const activeSpeakingSeconds = value.active_speaking_seconds;
  const presentationDurationSeconds = value.presentation_duration_seconds;
  const rawSegments = value.segments;
  if (
    !(averageSpeakingPace === null || (typeof averageSpeakingPace === "number" && Number.isFinite(averageSpeakingPace)))
    || !isNonNegativeInteger(finalizedWords)
    || !isNonNegativeFinite(activeSpeakingSeconds)
    || !isNonNegativeFinite(presentationDurationSeconds)
    || !Array.isArray(rawSegments)
  ) throw new Error("Invalid summary");
  const segments = rawSegments.map(parseSegment);
  return {
    averageSpeakingPace, finalizedWords, activeSpeakingSeconds,
    presentationDurationSeconds, segments,
  };
}

function parseSegment(value: unknown): SummarySegment {
  if (!isRecord(value)) throw new Error("Invalid segment");
  const text = value.text;
  const averageSpeakingPace = value.average_speaking_pace;
  const paceStatus = value.pace_status;
  if (
    typeof text !== "string" || text.trim().length === 0
    || !(averageSpeakingPace === null || isNonNegativeFinite(averageSpeakingPace))
    || !(paceStatus === null || paceStatus === "green" || paceStatus === "red")
    || (averageSpeakingPace === null) !== (paceStatus === null)
  ) throw new Error("Invalid segment");
  return { text, averageSpeakingPace, paceStatus };
}

function isNonNegativeFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return isNonNegativeFinite(value) && Number.isInteger(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stopStream(stream: StreamPort): void {
  for (const track of stream.getTracks()) track.stop();
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
