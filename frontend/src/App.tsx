import { useEffect, useReducer, useRef, type CSSProperties } from "react";

import { BrowserSession } from "./browserSession";
import {
  INITIAL_STATE,
  PACE_SCALE,
  markerPosition,
  reduceSession,
  scalePosition,
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
        <p className="target-copy">
          TARGET <strong>{PACE_SCALE.targetMinimum}–{PACE_SCALE.targetMaximum}</strong> WPM
        </p>
      </header>

      {state.lifecycle === "completed" ? (
        <CompletedView state={state} start={start} />
      ) : <>
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

      <PaceScale value={state.display?.wpm ?? null} variant="live" />

      <section className="session-controls" aria-label="Presentation controls">
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
      </>}
    </main>
  );
}

function CompletedView({
  state, start,
}: { readonly state: SessionState; readonly start: () => void }) {
  const summary = state.completedSummary;
  if (summary === null) return null;
  const inactive = state.completionReason === "inactivity";
  const empty = summary.finalizedWords === 0;
  return <section className="completed-view" aria-live="polite">
    <div className="summary">
    <p className="completion-title">{empty ? "No speech was detected" : "Presentation complete"}</p>
    <dl className="summary-metrics">
      <div><dt>Average WPM</dt><dd>{summary.averageSpeakingPace === null ? "—" : Math.round(summary.averageSpeakingPace)}</dd></div>
      <div><dt>Words</dt><dd>{summary.finalizedWords}</dd></div>
      <div><dt>Active speech</dt><dd>{formatDuration(summary.activeSpeakingSeconds)}</dd></div>
      <div><dt>Presentation duration</dt><dd>{formatDuration(summary.presentationDurationSeconds)}</dd></div>
    </dl>
    {!empty && summary.averageSpeakingPace === null && <p className="summary-note">Average WPM needs at least four seconds of active speech.</p>}
    {inactive && <p className="summary-note">The presentation ended after five minutes without recognized speech.</p>}
    </div>
    {!empty && <section className="segment-recap" aria-labelledby="pace-transcript-heading">
      <h2 id="pace-transcript-heading">Pace transcript</h2>
      <ol className="segment-list">
        {summary.segments.map((segment, index) => (
          <li className="summary-segment" key={index}>
            <div className="segment-copy">
              <p className="segment-text">{segment.text}</p>
            </div>
            <div className="segment-analysis">
              <div className="segment-pace">
                <span className="segment-value">
                  {segment.averageSpeakingPace === null ? "—" : Math.round(segment.averageSpeakingPace)} WPM
                </span>
                <span data-pace={segment.paceStatus ?? "neutral"}>
                  {segmentLabel(segment.averageSpeakingPace, segment.paceStatus)}
                </span>
              </div>
              <PaceScale value={segment.averageSpeakingPace} variant="compact" index={index + 1} />
            </div>
          </li>
        ))}
      </ol>
    </section>}
    <button className="primary-button" type="button" onClick={start}>Start new presentation</button>
  </section>;
}

function formatDuration(seconds: number): string {
  const wholeSeconds = Math.round(seconds);
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

function PaceScale({
  value, variant, index,
}: {
  readonly value: number | null;
  readonly variant: "live" | "compact";
  readonly index?: number;
}) {
  const available = value !== null;
  const showMarker = available || variant === "compact";
  const markerPositionStyle = available ? { left: `${markerPosition(value)}%` } : undefined;
  const targetStart = scalePosition(PACE_SCALE.targetMinimum);
  const targetEnd = scalePosition(PACE_SCALE.targetMaximum);
  const scaleStyle = {
    "--target-start": `${targetStart}%`,
    "--target-end": `${targetEnd}%`,
    "--target-width": `${targetEnd - targetStart}%`,
  } as CSSProperties;
  return (
    <section
      className={`pace-scale ${variant === "compact" ? "pace-scale-compact" : ""}`}
      style={scaleStyle}
      aria-label={
        `${variant === "compact" ? `Segment ${index ?? ""} pace scale` : "Pace scale"} from ${PACE_SCALE.minimum} to ${PACE_SCALE.maximum} words per minute; `
        + `target ${PACE_SCALE.targetMinimum} to ${PACE_SCALE.targetMaximum}`
      }
    >
      <div className="scale-track">
        <div className="target-zone" aria-hidden="true" />
        {showMarker && (
          <div
            className="pace-marker"
            data-testid={variant === "compact" ? "compact-pace-marker" : "pace-marker"}
            data-available={available}
            style={markerPositionStyle}
            aria-label={available
              ? `${variant === "compact" ? "Segment" : "Current"} pace ${Math.round(value)} words per minute`
              : "Segment pace unavailable"}
          />
        )}
      </div>
      {variant === "live" && <div className="scale-labels" aria-hidden="true">
        <span>{PACE_SCALE.minimum}</span>
        <span className="target-start">{PACE_SCALE.targetMinimum}</span>
        <span className="target-end">{PACE_SCALE.targetMaximum}</span>
        <span>{PACE_SCALE.maximum}</span>
      </div>}
      {variant === "live" && <div className="scale-guidance" aria-hidden="true">
        <span>TOO SLOW</span><span>ON PACE</span><span>TOO FAST</span>
      </div>}
    </section>
  );
}

function segmentLabel(wpm: number | null, status: "green" | "red" | null): string {
  const category = paceCategory(wpm, status);
  if (category === "unavailable") return "Pace unavailable";
  if (category === "on-pace") return "On pace";
  return category === "too-slow" ? "Too slow" : "Too fast";
}

function pacePresentation(state: SessionState): { status: string; direction: string } {
  if (state.lifecycle === "finalizing") {
    return { status: "FINALIZING…", direction: "Finishing the live transcript" };
  }
  if (state.display !== null) {
    const category = paceCategory(state.display.wpm, state.display.paceStatus);
    if (category === "on-pace") {
      return { status: "ON PACE", direction: "Keep this rhythm" };
    }
    return category === "too-slow"
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

function paceCategory(
  wpm: number | null,
  status: "green" | "red" | null,
): "unavailable" | "on-pace" | "too-slow" | "too-fast" {
  if (wpm === null || status === null) return "unavailable";
  if (status === "green") return "on-pace";
  return wpm < PACE_SCALE.targetMinimum ? "too-slow" : "too-fast";
}
