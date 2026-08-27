# Speech Speedometer

Speech Speedometer is a rhetoric-training web application that gives live
feedback about speaking pace. A React/TypeScript presentation UI streams
browser microphone audio through FastAPI to Deepgram and renders the backend's
deterministic live WPM and red/green pace classification.

The canonical live pace default is a Dual Window: 2 seconds at 20% blended
with 10 seconds at 80%, available after 1 active-speaking second. Single Window
is retained as a server-side fallback and tuning seam.

## Repository layout

```text
.
├── backend/          Python/FastAPI realtime service
│   ├── app/
│   ├── spikes/
│   ├── tests/
│   ├── .env.example
│   ├── .python-version
│   ├── pyproject.toml
│   └── uv.lock
├── frontend/         React/TypeScript Vite product UI
├── docs/             architecture decisions and empirical evidence
└── samples/          shared audio samples
```

The repository root is not a Python or Node project. The backend and frontend
own independent dependencies, lockfiles, and toolchains.

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

## Frontend setup

Install and run the frontend development server from `frontend/`:

```bash
cd frontend
npm install
npm run dev
```

Vite serves http://localhost:5173 and proxies `/ws/live` to FastAPI on port
8000. Use `npm test`, `npm run typecheck`, and `npm run build` for frontend
verification.

## Local product flow

Set `DEEPGRAM_API_KEY` in `backend/.env`. For development, run FastAPI and Vite
separately and open http://localhost:5173/. For a production-style local run,
build the frontend first and let FastAPI serve `frontend/dist`:

```bash
npm --prefix frontend run build
uv run --directory backend uvicorn app.main:app --reload
```

Open http://localhost:8000/ in current desktop Chrome or Chromium. Localhost is
treated as a secure context for microphone access. Press Start, grant microphone
permission, speak, then press Stop. The product keeps the most recent valid
pace visible through pauses and waits for provider finalization before showing
completion. Completion replaces the dominant live reading with a scrollable
summary containing Average WPM, finalized words, active speech, presentation
duration, and the complete chronological transcript grouped into deterministic
pace segments. Each segment shows rounded WPM, an On pace/Too slow/Too fast
label, and a compact marker on the same fixed pace scale, with transcript text
in the left column and pace analysis in the right column on desktop. Empty presentations
say `No speech was detected`; presentations below four seconds of active speech
show their word/time metrics but leave Average WPM unavailable. The browser
reveals this summary only after Deepgram has drained and the backend has sent
ordered `summary` then `stopped` events. After five minutes without recognized
speech progress it requests the same graceful finalization and explains the
inactivity ending. The browser records
`audio/webm;codecs=opus`; unsupported browsers fail before requesting the
microphone. A clean stopped state is shown only after Deepgram drains the final
audio, emits Metadata, and closes normally.

The browser never receives the Deepgram credential. See the
[transport acceptance procedure](docs/07_BROWSER_LIVE_WPM_ACCEPTANCE.md) for
the reproducible real-browser check.

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
WebM/Opus Blob approximately every 100 ms
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

`MediaRecorder.start(100)` defines the approximate transport cadence, not the
WPM clock. FastAPI forwards the containerized `audio/webm;codecs=opus` bytes
without transcoding. WPM is calculated from Deepgram's word timestamps on the
audio timeline, never from network receive time. During a pause no changed word
timeline is emitted, so the UI retains the last valid WPM value.

At finalization, the immutable finalized-word timeline goes separately to
`SessionSummaryCalculator`. It reuses the active-speech gap policy, calculates
presentation duration from first word start to last word end, and emits the
unrounded summary metrics. Non-empty final provider chunks also retain their
formatted text and whole-word boundaries. The calculator greedily groups whole
chunks to at least four active-speech seconds where possible, merges a short
tail backward, and calculates each segment independently. It never averages the
rolling live WPM values or performs sentence/NLP splitting.

## Product and architecture

Start with [the architecture decisions](docs/01_ARCHITECTURE_DECISIONS.md).
The provider decision is supported by the recorded
[Azure live-transcription findings](docs/03_GPT_LIVE_TRANSCRIBE_FINDINGS.md),
[Azure Whisper comparison](docs/04_GPT_REALTIME_WHISPER_FINDINGS.md), and
[Deepgram Nova-3 findings](docs/06_DEEPGRAM_NOVA_3_FINDINGS.md).

## Contributing

GitHub Issues is the source of truth for planned work, decisions attached to
work, and progress. Start with an existing issue or create one using the
repository's bug, feature, or implementation-task form. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the issue-to-pull-request workflow.
