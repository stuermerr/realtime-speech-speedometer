# Speech Speedometer

Speech Speedometer is a rhetoric-training web application that gives live
feedback about speaking pace. The repository is a small monorepo with separate
tooling boundaries for the Python backend and the future React/TypeScript
frontend.

## Repository layout

```text
.
├── backend/          Python/FastAPI uv project
│   ├── app/
│   ├── spikes/
│   ├── tests/
│   ├── .env.example
│   ├── .python-version
│   ├── pyproject.toml
│   └── uv.lock
├── frontend/         future React/TypeScript project
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

Use `--directory backend` for root-level commands. `--project backend` selects
the backend environment but does not change the command's working directory,
so the flat `app` and `spikes` imports will not resolve reliably.

## Product and architecture

Start with [the project brief](docs/00_PROJECT_BRIEF.md), then read
[the architecture decisions](docs/01_ARCHITECTURE_DECISIONS.md). Provider spike
evidence is recorded in the remaining documents under `docs/`.

## Contributing

GitHub Issues is the source of truth for planned work, decisions attached to
work, and progress. Start with an existing issue or create one using the
repository's bug, feature, or implementation-task form. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the issue-to-pull-request workflow.
