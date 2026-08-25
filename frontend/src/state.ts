export type PaceStatus = "green" | "red";
export const PACE_SCALE = {
  minimum: 60,
  targetMinimum: 115,
  targetMaximum: 150,
  maximum: 220,
} as const;
export type Lifecycle =
  | "idle"
  | "starting"
  | "listening"
  | "finalizing"
  | "completed"
  | "error"
  | "unsupported";

export interface PaceDisplay {
  readonly wpm: number;
  readonly paceStatus: PaceStatus;
}

export interface SessionState {
  readonly lifecycle: Lifecycle;
  readonly display: PaceDisplay | null;
  readonly error: string | null;
}

export type SessionAction =
  | { readonly type: "start" }
  | { readonly type: "listening" }
  | { readonly type: "stop" }
  | { readonly type: "stopped" }
  | {
      readonly type: "measurement";
      readonly wpm: number | null;
      readonly paceStatus: PaceStatus | null;
    }
  | { readonly type: "fail"; readonly message: string }
  | { readonly type: "unsupported"; readonly message: string };

export const INITIAL_STATE: SessionState = {
  lifecycle: "idle",
  display: null,
  error: null,
};

export function reduceSession(
  state: SessionState,
  action: SessionAction,
): SessionState {
  switch (action.type) {
    case "start":
      return { lifecycle: "starting", display: null, error: null };
    case "listening":
      return { ...state, lifecycle: "listening", error: null };
    case "stop":
      return { ...state, lifecycle: "finalizing" };
    case "stopped":
      return { ...state, lifecycle: "completed" };
    case "measurement": {
      const wpmAvailable = action.wpm !== null;
      const statusAvailable = action.paceStatus !== null;
      if (
        wpmAvailable !== statusAvailable ||
        (action.wpm !== null && !Number.isFinite(action.wpm))
      ) {
        return protocolError(state);
      }
      if (action.wpm === null || action.paceStatus === null) {
        return state;
      }
      return {
        ...state,
        display: { wpm: action.wpm, paceStatus: action.paceStatus },
      };
    }
    case "fail":
      return { ...state, lifecycle: "error", error: action.message };
    case "unsupported":
      return { ...state, lifecycle: "unsupported", error: action.message };
  }
}

export function markerPosition(wpm: number): number {
  return Math.min(100, Math.max(0, scalePosition(wpm)));
}

export function scalePosition(wpm: number): number {
  return (
    ((wpm - PACE_SCALE.minimum) / (PACE_SCALE.maximum - PACE_SCALE.minimum))
    * 100
  );
}

function protocolError(state: SessionState): SessionState {
  return {
    ...state,
    lifecycle: "error",
    error: "The live session sent an invalid measurement.",
  };
}
