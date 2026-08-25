import { useEffect, useReducer, useRef } from "react";

import { BrowserSession } from "./browserSession";
import {
  INITIAL_STATE,
  markerPosition,
  reduceSession,
  type SessionAction,
  type SessionState,
} from "./state";

export interface SessionControl {
  start(): Promise<void>;
  stop(): void;
  cleanup(): void;
}

type SessionFactory = (dispatch: (action: SessionAction) => void) => SessionControl;

export interface AppProps {
  readonly createSession?: SessionFactory;
}

const defaultSessionFactory: SessionFactory = (dispatch) =>
  new BrowserSession(dispatch);

export function App({ createSession = defaultSessionFactory }: AppProps) {
  const [state, dispatch] = useReducer(reduceSession, INITIAL_STATE);
  const session = useRef<SessionControl | null>(null);

  useEffect(() => () => session.current?.cleanup(), []);

  const start = (): void => {
    session.current?.cleanup();
    dispatch({ type: "start" });
    const nextSession = createSession(dispatch);
    session.current = nextSession;
    void nextSession.start();
  };

  const stop = (): void => {
    dispatch({ type: "stop" });
    session.current?.stop();
  };

  const presentation = pacePresentation(state);
  const showStart = ["idle", "completed", "error"].includes(state.lifecycle);
  const startLabel = state.lifecycle === "completed"
    ? "Start new presentation"
    : state.lifecycle === "error"
      ? "Try again"
      : "Start presentation";

  return (
    <main className="speedometer" data-lifecycle={state.lifecycle}>
      <header className="masthead">
        <div className="brand-mark" aria-hidden="true">S</div>
        <div>
          <p className="eyebrow">LIVE RHETORIC TRAINING</p>
          <h1>Speech Speedometer</h1>
        </div>
        <p className="target-copy">TARGET <strong>115–150</strong> WPM</p>
      </header>

      <section
        className="live-reading"
        data-pace={state.display?.paceStatus ?? "neutral"}
        aria-live="polite"
        aria-atomic="true"
      >
        <div className="number-row">
          <span className="wpm-number">{state.display === null ? "—" : Math.round(state.display.wpm)}</span>
          <span className="wpm-unit">WPM</span>
        </div>
        <p className="pace-status">{presentation.status}</p>
        <p className="pace-direction">{presentation.direction}</p>
      </section>

      <PaceScale state={state} />

      <section className="session-controls" aria-label="Presentation controls">
        {state.lifecycle === "completed" && (
          <p className="completion-title">Presentation complete</p>
        )}
        {(state.lifecycle === "error" || state.lifecycle === "unsupported") && (
          <div role="alert" className="error-panel">
            <p>{state.lifecycle === "unsupported" ? "Browser not supported" : "Session interrupted"}</p>
            <span>{state.error}</span>
          </div>
        )}
        {showStart && (
          <button className="primary-button" type="button" onClick={start}>
            {startLabel}
          </button>
        )}
        {state.lifecycle === "starting" && (
          <button className="primary-button" type="button" disabled>
            Starting…
          </button>
        )}
        {state.lifecycle === "listening" && (
          <button className="stop-button" type="button" onClick={stop}>
            <span aria-hidden="true" /> Stop presentation
          </button>
        )}
      </section>
    </main>
  );
}

function PaceScale({ state }: { readonly state: SessionState }) {
  const marker = state.display === null ? null : markerPosition(state.display.wpm);
  return (
    <section className="pace-scale" aria-label="Pace scale from 60 to 220 words per minute">
      <div className="scale-track">
        <div className="target-zone" aria-hidden="true" />
        {marker !== null && (
          <div
            className="pace-marker"
            data-testid="pace-marker"
            style={{ left: `${marker}%` }}
            aria-label={`Current pace ${Math.round(state.display?.wpm ?? 0)} words per minute`}
          />
        )}
      </div>
      <div className="scale-labels" aria-hidden="true">
        <span>60</span><span className="target-start">115</span><span className="target-end">150</span><span>220</span>
      </div>
      <div className="scale-guidance" aria-hidden="true">
        <span>TOO SLOW</span><span>ON PACE</span><span>TOO FAST</span>
      </div>
    </section>
  );
}

function pacePresentation(state: SessionState): { status: string; direction: string } {
  if (state.lifecycle === "finalizing") {
    return { status: "FINALIZING…", direction: "Finishing the live transcript" };
  }
  if (state.display !== null) {
    if (state.display.paceStatus === "green") {
      return { status: "ON PACE", direction: "Keep this rhythm" };
    }
    return state.display.wpm < 115
      ? { status: "TOO SLOW", direction: "Pick up the pace" }
      : { status: "TOO FAST", direction: "Slow down" };
  }
  if (state.lifecycle === "starting" || state.lifecycle === "listening") {
    return { status: "CALCULATING…", direction: "Start speaking to set your pace" };
  }
  if (state.lifecycle === "completed") {
    return { status: "COMPLETE", direction: "Your final pace is held above" };
  }
  return { status: "READY", direction: "Press Start when you are ready" };
}
