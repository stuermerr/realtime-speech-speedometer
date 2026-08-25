# Speech Speedometer

Speech Speedometer is a rhetoric-training web application that gives live
feedback about speaking pace. The current local tracer bullet includes a
FastAPI-served vanilla browser adapter; a future polished frontend remains a
separate tooling boundary.

## Repository layout

```text
.
├── backend/          Python/FastAPI uv project and temporary debug client
│   ├── app/
│   ├── spikes/
│   ├── tests/
│   ├── .env.example
│   ├── .python-version
│   ├── pyproject.toml
│   └── uv.lock
├── frontend/         reserved for the future polished React/TypeScript UI
├── docs/             product, architecture, and spike evidence
└── samples/          shared audio samples
```

The repository root is not a Python or Node project. The backend owns its
dependencies, lockfile, virtual environment, and local environment file. When
the frontend is initialized, it will own a separate `package.json`, Node
lockfile, and `node_modules` directory; it will not use uv.

## Backend setup

Create or reconcile the backend environment from `backend/`:

```bash
cd backend
cp .env.example .env
uv sync --locked
```

Fill `backend/.env` with local credentials. Process environment variables take
precedence over values in that file. Both `.env` and `.venv` are disposable,
ignored local state; never move a virtual environment between directories.
Recreate it with `uv sync` if its location or Python interpreter is wrong.

## Backend commands

Commands can be run from either working directory.

| Task | From `backend/` | From repository root |
| --- | --- | --- |
| Sync | `uv sync --locked` | `uv sync --directory backend --locked` |
| Add dependency | `uv add <package>` | `uv add --directory backend <package>` |
| Run server | `uv run uvicorn app.main:app --reload` | `uv run --directory backend uvicorn app.main:app --reload` |
| Run tests | `uv run --locked pytest` | `uv run --directory backend --locked pytest` |
| Type-check | `uv run --locked mypy app tests spikes` | `uv run --directory backend --locked mypy app tests spikes` |
| Azure spike | `uv run python -m spikes.run_realtime_transcription` | `uv run --directory backend python -m spikes.run_realtime_transcription` |
| Deepgram spike | `uv run python -m spikes.run_deepgram_transcription` | `uv run --directory backend python -m spikes.run_deepgram_transcription` |

## Local browser tracer bullet

Set `DEEPGRAM_API_KEY` in `backend/.env`, then start the application from the
repository root:

```bash
uv run --directory backend uvicorn app.main:app --reload
```

Open http://localhost:8000/ in current desktop Chrome or Chromium. Localhost is
treated as a secure context for microphone access. Press Start, grant microphone
permission, speak, then press Stop. The browser records
`audio/webm;codecs=opus`; unsupported browsers fail before requesting the
microphone. A clean stopped state is shown only after Deepgram drains the final
audio, emits Metadata, and closes normally.

The browser never receives the Deepgram credential. See
[the manual acceptance procedure](docs/07_BROWSER_LIVE_WPM_ACCEPTANCE.md) for
the reproducible real-browser checks.

Use `--directory backend` for root-level commands. `--project backend` selects
the backend environment but does not change the command's working directory,
so the flat `app` and `spikes` imports will not resolve reliably.

## Complete realtime data flow

```text
YOUR VOICE
  ↓
Microphone hardware
  ↓
Browser navigator.mediaDevices.getUserMedia()
  ↓
MediaStream
  ↓
MediaRecorder
  ↓
WebM/Opus Blob approximately every 250 ms
  ↓
Browser WebSocket
  ↓
Binary WebSocket frame
  ↓
FastAPI WebSocket
  ↓
Python receives bytes
  ↓
Deepgram WebSocket
  ↓
Same audio bytes (no transcoding)
  ↓
Deepgram Nova-3
  ↓
Results event
  ↓
Words + audio-timeline timestamps
  ↓
ParsedDeepgramResult
  ↓
SessionWordState
  ↓
ActiveSpeechWpm.calculate(...)
  ↓
WpmMeasurement
  ↓
FastAPI serializes measurement JSON
  ↓
Browser WebSocket
  ↓
Text frame
  ↓
JavaScript receives and parses the message
  ↓
JavaScript updates the HTML
  ↓
USER SEES "WPM: 132"
```

`MediaRecorder.start(250)` defines the approximate transport cadence, not the
WPM clock. FastAPI forwards the containerized `audio/webm;codecs=opus` bytes
without transcoding. WPM is calculated from Deepgram's word timestamps on the
audio timeline, never from network receive time. During a pause no changed word
timeline is emitted, so the UI retains the last valid WPM value.

## Product and architecture

Start with [the project brief](docs/00_PROJECT_BRIEF.md), then read
[the architecture decisions](docs/01_ARCHITECTURE_DECISIONS.md). Provider spike
evidence is recorded in the remaining documents under `docs/`.

## Contributing

GitHub Issues is the source of truth for planned work, decisions attached to
work, and progress. Start with an existing issue or create one using the
repository's bug, feature, or implementation-task form. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the issue-to-pull-request workflow.
