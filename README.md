# Speech Speedometer

Speech Speedometer is a rhetoric-training web application that gives speakers
live feedback on their speaking pace. It streams microphone audio from a React
client through a FastAPI backend to Deepgram, then calculates words per minute
(WPM) from the returned word timestamps.

## Why use it?

- See a responsive live WPM reading while speaking.
- Get simple pace feedback: **on pace** at 115–150 WPM and **too slow** or
  **too fast** outside that range.
- Keep the last meaningful reading visible during pauses.
- Review a completed-session summary with average pace, finalized word count,
  active speaking time, presentation duration, transcript, and pace segments.
- Keep speech-provider credentials on the server rather than in the browser.
- Rely on deterministic, testable pace calculations instead of an LLM.

The application stores session state in memory only. It has no accounts,
database, or presentation history.

## How it works

```text
Browser microphone
        │ WebM/Opus audio over WebSocket
        ▼
FastAPI backend ───────────► Deepgram Nova-3
        ▲                      │
        │ live WPM + summary   │ transcript and word timestamps
        └──────────────────────┘
```

The browser records approximately 100 ms audio chunks. FastAPI relays those
chunks to Deepgram without transcoding. The backend uses Deepgram's audio
timeline—not network arrival time—to calculate recent active-speaking pace.
The default calculation blends a 2-second window (20%) with a 10-second window
(80%) and becomes available after one second of active speech.

For the rationale and provider evidence, see
[the architecture decision log](docs/01_ARCHITECTURE_DECISIONS.md) and the
[Deepgram Nova-3 findings](docs/06_DEEPGRAM_NOVA_3_FINDINGS.md).

## Prerequisites

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- A Deepgram API key
- A current desktop browser with microphone access and
  `audio/webm;codecs=opus` recording support (Chrome or Chromium is the tested
  local path)

An internet connection is required while using live transcription. Your
Deepgram account may incur usage charges.

## Get started

Clone the repository and install the independent backend and frontend
dependencies:

```bash
git clone https://github.com/stuermerr/realtime-speech-speedometer.git
cd realtime-speech-speedometer

cp backend/.env.example backend/.env
uv sync --directory backend --locked
npm --prefix frontend ci
```

Open `backend/.env` and replace the placeholder value:

```dotenv
DEEPGRAM_API_KEY=your-deepgram-api-key
```

The `.env` file is ignored by Git. Process environment variables override
values loaded from it.

### Run in development

Start the backend in one terminal:

```bash
uv run --directory backend uvicorn app.main:app --reload
```

Start the Vite development server in another:

```bash
npm --prefix frontend run dev
```

Open <http://localhost:5173>, select **Start**, allow microphone access, and
speak. Select **Stop** to finalize the transcript and view the session summary.
Vite proxies `/ws/live` to FastAPI at `http://localhost:8000`.

### Run the built frontend through FastAPI

```bash
npm --prefix frontend run build
uv run --directory backend uvicorn app.main:app
```

Open <http://localhost:8000>. The backend health check is available at
<http://localhost:8000/health>.

## Configuration

The accepted defaults work without additional configuration. Optional backend
environment variables are documented in
[`backend/.env.example`](backend/.env.example). They include live-WPM tuning and
secret-safe diagnostic logging. Restart the backend after changing pace
settings.

The Azure variables in that file belong only to the historical transcription
spike; the product uses Deepgram.

## Development

The repository root is not itself a Python or Node project. Use
`--directory backend` for root-level uv commands and `--prefix frontend` for
root-level npm commands.

### Verify the backend

```bash
uv run --directory backend --locked pytest
uv run --directory backend --locked mypy app tests spikes
uv run --directory backend --locked ruff check app tests spikes
```

### Verify the frontend

```bash
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

The automated suites do not require a live microphone or a Deepgram
connection. Use the
[browser acceptance procedure](docs/07_BROWSER_LIVE_WPM_ACCEPTANCE.md) when a
change requires an end-to-end microphone check.

## Project structure

```text
.
├── backend/
│   ├── app/          FastAPI application and pace/session services
│   ├── spikes/       Realtime transcription experiments
│   └── tests/        Backend unit and integration tests
├── frontend/
│   └── src/          React UI, browser session adapter, and tests
├── docs/             Architecture decisions and provider evidence
├── CONTRIBUTING.md
└── README.md
```

## Documentation and help

- [Architecture decisions](docs/01_ARCHITECTURE_DECISIONS.md)
- [Azure live-transcription findings](docs/03_GPT_LIVE_TRANSCRIBE_FINDINGS.md)
- [Azure Whisper comparison](docs/04_GPT_REALTIME_WHISPER_FINDINGS.md)
- [Deepgram Nova-3 findings](docs/06_DEEPGRAM_NOVA_3_FINDINGS.md)
- [Browser acceptance procedure](docs/07_BROWSER_LIVE_WPM_ACCEPTANCE.md)

For bugs, setup questions, or feature requests, search the
[GitHub issues](https://github.com/stuermerr/realtime-speech-speedometer/issues)
and open an issue if one does not already cover the topic. Do not include API
keys, transcripts, or other sensitive data in issue reports.

## Maintainer and contributing

Speech Speedometer is maintained by
[@stuermerr](https://github.com/stuermerr). Contributions are welcome through
the issue-and-pull-request workflow described in
[`CONTRIBUTING.md`](CONTRIBUTING.md). Please discuss substantial changes in an
issue before implementation and keep provider secrets out of commits and logs.
