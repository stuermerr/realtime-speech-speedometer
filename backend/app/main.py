from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import (
    ConfigurationError,
    DeepgramSettings,
    LiveWpmDebugSettings,
)
from app.services.browser_session import (
    BrowserDeepgramSession,
    BrowserLiveWpmSession,
    LiveWpmDiagnostics,
)
from app.services.deepgram_transcription import (
    DeepgramAudioMode,
    DeepgramTranscriptionSession,
)


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[2] / "frontend" / "dist"
ProviderFactory = Callable[[], BrowserDeepgramSession]


def _browser_provider() -> BrowserDeepgramSession:
    return DeepgramTranscriptionSession(
        DeepgramSettings.from_environment(),
        audio_mode=DeepgramAudioMode.WEBM_OPUS,
    )


def create_app(
    *,
    provider_factory: ProviderFactory | None = None,
    frontend_directory: Path = FRONTEND_DIRECTORY,
) -> FastAPI:
    application = FastAPI(title="Speech Speedometer")
    make_provider = _browser_provider if provider_factory is None else provider_factory
    debug_logging_enabled = LiveWpmDebugSettings.from_environment().enabled
    assets_directory = frontend_directory / "assets"
    if assets_directory.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=assets_directory),
            name="frontend-assets",
        )

    @application.get("/", response_class=FileResponse)
    async def product_client() -> FileResponse:
        index = frontend_directory / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail="Frontend build is unavailable; run npm run build in frontend",
            )
        return FileResponse(index)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.websocket("/ws/live")
    async def live_wpm(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            provider = make_provider()
        except ConfigurationError:
            await websocket.send_json(
                {"type": "error", "message": "Live transcription is not configured"}
            )
        else:
            await BrowserLiveWpmSession(
                websocket,
                provider,
                diagnostics=LiveWpmDiagnostics(enabled=debug_logging_enabled),
            ).run()
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass

    return application


app = create_app()
